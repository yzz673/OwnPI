from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from ._validation import require_exact_keys
from .contracts import (
    InternalMemoryConflictStatus,
    InternalMemoryFreshness,
    InternalMemoryState,
    parse_internal_ref,
)
from .records import InternalMemoryRecord, remove_internal_relation_ref
from .locking import filesystem_write_lock
from .storage import StoredInternalMemoryRecord, canonical_index_bytes, verify_stored_body


class InternalMemoryRepositoryStatus(str, Enum):
    EMPTY = "empty"
    AVAILABLE = "available"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class InternalMemoryRepositorySnapshot:
    status: InternalMemoryRepositoryStatus
    identity: str
    record_count: int
    committed_count: int
    diagnostic: str = ""


class InternalMemoryRepositoryError(RuntimeError):
    pass


class InternalMemoryRepositoryCorruptError(InternalMemoryRepositoryError):
    pass


class InternalMemoryRepository:
    """Single writable authority for canonical Internal Memory records."""

    INDEX_FILE = "index.json"
    OBJECTS_DIR = "objects"
    WRITE_LOCK_FILE = ".repository.lock"

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("internal memory root must be a Path")
        if not root.is_absolute():
            raise ValueError("internal memory root must be absolute")
        self._root = root
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root

    def snapshot(self) -> InternalMemoryRepositorySnapshot:
        try:
            records = self._load_records()
        except InternalMemoryRepositoryCorruptError as exc:
            return InternalMemoryRepositorySnapshot(
                status=InternalMemoryRepositoryStatus.CORRUPT,
                identity="",
                record_count=0,
                committed_count=0,
                diagnostic=str(exc),
            )
        except OSError as exc:
            return InternalMemoryRepositorySnapshot(
                status=InternalMemoryRepositoryStatus.UNAVAILABLE,
                identity="",
                record_count=0,
                committed_count=0,
                diagnostic=type(exc).__name__,
            )
        canonical = canonical_index_bytes(records)
        return InternalMemoryRepositorySnapshot(
            status=(
                InternalMemoryRepositoryStatus.AVAILABLE
                if records
                else InternalMemoryRepositoryStatus.EMPTY
            ),
            identity=hashlib.sha256(canonical).hexdigest(),
            record_count=len(records),
            committed_count=sum(
                item.state == InternalMemoryState.COMMITTED for item in records
            ),
        )

    def create_candidate(self, record: InternalMemoryRecord) -> None:
        if record.state != InternalMemoryState.CANDIDATE:
            raise ValueError("new records must enter the repository as candidates")
        with self._write_transaction():
            records = list(self._load_records())
            self._reject_duplicate(records, record.logical_ref)
            self._persist(records + [StoredInternalMemoryRecord.from_record(record)], (record,))

    def replace_candidate(self, record: InternalMemoryRecord) -> None:
        if record.state != InternalMemoryState.CANDIDATE:
            raise ValueError("candidate replacement must remain a candidate")
        with self._write_transaction():
            records = list(self._load_records())
            position = self._position(records, record.logical_ref)
            if records[position].state != InternalMemoryState.CANDIDATE:
                raise ValueError("only candidate records can be replaced")
            records[position] = StoredInternalMemoryRecord.from_record(record)
            self._persist(records, (record,))

    def get(self, logical_ref: str) -> InternalMemoryRecord | None:
        parse_internal_ref(logical_ref)
        records = self._load_records()
        stored = next((item for item in records if item.logical_ref == logical_ref), None)
        return self._load_record(stored) if stored is not None else None

    def committed_metadata(self) -> tuple[StoredInternalMemoryRecord, ...]:
        return tuple(
            item
            for item in self._load_records()
            if item.state == InternalMemoryState.COMMITTED
        )

    def read_committed(self, logical_ref: str) -> InternalMemoryRecord | None:
        parse_internal_ref(logical_ref)
        stored = next(
            (
                item
                for item in self._load_records()
                if item.logical_ref == logical_ref
                and item.state == InternalMemoryState.COMMITTED
            ),
            None,
        )
        return self._load_record(stored) if stored is not None else None

    def _records_for_consolidation(self) -> tuple[InternalMemoryRecord, ...]:
        return tuple(self._load_record(item) for item in self._load_records())

    def _replace_for_consolidation(self, record: InternalMemoryRecord) -> None:
        with self._write_transaction():
            records = list(self._load_records())
            position = self._position(records, record.logical_ref)
            records[position] = StoredInternalMemoryRecord.from_record(record)
            self._persist(records, (record,))

    def _commit(
        self,
        logical_ref: str,
        *,
        freshness: InternalMemoryFreshness | None = None,
    ) -> InternalMemoryRecord:
        with self._write_transaction():
            records = list(self._load_records())
            position = self._position(records, logical_ref)
            current = self._load_record(records[position])
            if current.state != InternalMemoryState.CANDIDATE:
                raise ValueError("only candidate records can be committed")
            if current.relations.conflict_status == InternalMemoryConflictStatus.NEEDS_VERIFICATION:
                raise ValueError("unresolved conflict cannot be committed")
            committed = replace(
                current,
                state=InternalMemoryState.COMMITTED,
                freshness=freshness or current.freshness,
            )
            records[position] = StoredInternalMemoryRecord.from_record(committed)
            self._persist(records, ())
            return committed

    def _archive(
        self,
        logical_ref: str,
        *,
        freshness: InternalMemoryFreshness | None = None,
    ) -> InternalMemoryRecord:
        with self._write_transaction():
            records = list(self._load_records())
            position = self._position(records, logical_ref)
            current = self._load_record(records[position])
            if current.state == InternalMemoryState.ARCHIVED:
                raise ValueError("record is already archived")
            archived = replace(
                current,
                state=InternalMemoryState.ARCHIVED,
                freshness=freshness or current.freshness,
            )
            records[position] = StoredInternalMemoryRecord.from_record(archived)
            self._persist(records, ())
            return archived

    def _supersede(
        self,
        *,
        prior_ref: str,
        replacement_ref: str,
        freshness: InternalMemoryFreshness,
    ) -> tuple[InternalMemoryRecord, InternalMemoryRecord]:
        if prior_ref == replacement_ref:
            raise ValueError("a record cannot supersede itself")
        with self._write_transaction():
            records = list(self._load_records())
            prior_position = self._position(records, prior_ref)
            replacement_position = self._position(records, replacement_ref)
            prior = self._load_record(records[prior_position])
            replacement = self._load_record(records[replacement_position])
            if prior.state != InternalMemoryState.COMMITTED:
                raise ValueError("superseded record must be committed")
            if replacement.state != InternalMemoryState.CANDIDATE:
                raise ValueError("replacement record must be a candidate")
            if (
                replacement.relations.conflict_status
                == InternalMemoryConflictStatus.NEEDS_VERIFICATION
            ):
                raise ValueError("unresolved replacement conflict cannot be committed")
            promoted = replace(
                replacement,
                state=InternalMemoryState.COMMITTED,
                freshness=freshness,
                relations=replace(
                    replacement.relations,
                    supersedes=tuple(
                        dict.fromkeys(replacement.relations.supersedes + (prior_ref,))
                    ),
                ),
            )
            archived = replace(
                prior,
                state=InternalMemoryState.ARCHIVED,
                freshness=freshness,
                relations=replace(prior.relations, superseded_by=replacement_ref),
            )
            records[prior_position] = StoredInternalMemoryRecord.from_record(archived)
            records[replacement_position] = StoredInternalMemoryRecord.from_record(promoted)
            self._persist(records, ())
            return archived, promoted

    def _delete(self, logical_ref: str) -> bool:
        parse_internal_ref(logical_ref)
        with self._write_transaction():
            records = list(self._load_records())
            matching = [item for item in records if item.logical_ref == logical_ref]
            if not matching:
                return False
            retained = [
                StoredInternalMemoryRecord.from_record(
                    remove_internal_relation_ref(self._load_record(item), logical_ref)
                )
                for item in records
                if item.logical_ref != logical_ref
            ]
            self._persist(retained, ())
            digest = matching[0].body_sha256
            if not any(item.body_sha256 == digest for item in retained):
                self._body_path(digest).unlink(missing_ok=True)
            return True

    def _load_records(self) -> tuple[StoredInternalMemoryRecord, ...]:
        self._validate_root(for_write=False)
        index_path = self._root / self.INDEX_FILE
        if not index_path.exists():
            return ()
        if index_path.is_symlink() or not index_path.is_file():
            raise InternalMemoryRepositoryCorruptError("internal index is not a regular file")
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            data = require_exact_keys(
                payload,
                required=frozenset({"schema_version", "records"}),
                label="internal memory index",
            )
            if data["schema_version"] != 1 or not isinstance(data["records"], list):
                raise ValueError("unsupported internal memory index schema")
            records = tuple(
                StoredInternalMemoryRecord.from_dict(item) for item in data["records"]
            )
            refs = tuple(item.logical_ref for item in records)
            if len(set(refs)) != len(refs):
                raise ValueError("internal memory index contains duplicate identities")
            if refs != tuple(sorted(refs)):
                raise ValueError("internal memory index is not canonically ordered")
            for item in records:
                body_path = self._body_path(item.body_sha256)
                if body_path.is_symlink() or not body_path.is_file():
                    raise ValueError("internal memory body object is missing or unsafe")
                verify_stored_body(body_path, item.body_sha256)
            return records
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise InternalMemoryRepositoryCorruptError("internal memory store is corrupt") from exc

    def _load_record(self, stored: StoredInternalMemoryRecord) -> InternalMemoryRecord:
        body_path = self._body_path(stored.body_sha256)
        if body_path.is_symlink() or not body_path.is_file():
            raise InternalMemoryRepositoryCorruptError("internal memory body object is unavailable")
        try:
            return stored.to_record(body_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise InternalMemoryRepositoryCorruptError("internal memory body object is corrupt") from exc

    def _persist(
        self,
        records: list[StoredInternalMemoryRecord],
        bodies: tuple[InternalMemoryRecord, ...],
    ) -> None:
        self._validate_root(for_write=True)
        for record in bodies:
            self._write_body(record)
        _atomic_write_bytes(self._root / self.INDEX_FILE, canonical_index_bytes(tuple(records)))

    def _write_body(self, record: InternalMemoryRecord) -> None:
        body = record.body.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        path = self._body_path(digest)
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != body:
                raise InternalMemoryRepositoryCorruptError("body object collision or unsafe path")
            return
        _atomic_write_bytes(path, body)

    def _body_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise InternalMemoryRepositoryCorruptError("invalid body object identity")
        return self._root / self.OBJECTS_DIR / f"{digest}.txt"

    def _validate_root(self, *, for_write: bool) -> None:
        if self._root.exists() and (self._root.is_symlink() or not self._root.is_dir()):
            raise InternalMemoryRepositoryCorruptError("internal memory root is unsafe")
        objects = self._root / self.OBJECTS_DIR
        if objects.exists() and (objects.is_symlink() or not objects.is_dir()):
            raise InternalMemoryRepositoryCorruptError("internal object root is unsafe")
        if for_write:
            self._root.mkdir(parents=True, exist_ok=True)
            objects.mkdir(exist_ok=True)

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        with self._lock:
            self._validate_root(for_write=True)
            with filesystem_write_lock(self._root / self.WRITE_LOCK_FILE):
                yield

    @staticmethod
    def _position(records: list[StoredInternalMemoryRecord], logical_ref: str) -> int:
        parse_internal_ref(logical_ref)
        for position, record in enumerate(records):
            if record.logical_ref == logical_ref:
                return position
        raise KeyError(logical_ref)

    @staticmethod
    def _reject_duplicate(
        records: list[StoredInternalMemoryRecord], logical_ref: str
    ) -> None:
        if any(item.logical_ref == logical_ref for item in records):
            raise ValueError("internal memory identity already exists")
def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise InternalMemoryRepositoryCorruptError("refusing to replace a symlink")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
