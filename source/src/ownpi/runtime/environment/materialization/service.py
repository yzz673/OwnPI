from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import asyncio
import time
from typing import AsyncIterator

from ownpi.runtime.environment.materialization.file_io import MaterializationFileIO
from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.policy import lexical_absolute_path

from ownpi.runtime.environment.materialization.contracts import (
    MaterializationAdapter,
    MaterializationFailure,
    MaterializationReceipt,
    MaterializationResult,
)


class MaterializationService:
    def __init__(
        self,
        *,
        cache_root: Path,
        effective_permissions: EffectivePermissions | None = None,
        lock_timeout_seconds: int = 30,
        max_receipt_chars: int = 16_384,
    ) -> None:
        self._cache_root = lexical_absolute_path(cache_root)
        self._io = MaterializationFileIO(effective_permissions)
        self._lock_timeout_seconds = lock_timeout_seconds
        self._max_receipt_chars = max_receipt_chars

    async def materialize(self, adapter: MaterializationAdapter) -> MaterializationResult:
        try:
            return await self._materialize(adapter)
        except PermissionError as exc:
            return MaterializationResult(
                False, error=str(exc), failure_owner='policy_owned',
                failure_code='materialization_permission_denied',
            )

    async def _materialize(self, adapter: MaterializationAdapter) -> MaterializationResult:
        resolution_staging = self._private_resolution(adapter.resource_kind)
        try:
            resolved_revision = await adapter.resolve_revision(resolution_staging)
        except MaterializationFailure as exc:
            return MaterializationResult(
                False,
                error=str(exc),
                failure_owner=exc.owner,
                failure_code=exc.code,
            )
        finally:
            self._io.remove_tree(resolution_staging)
        key = _resource_key(
            adapter.resource_kind,
            adapter.resource_identity,
            resolved_revision,
            adapter.cache_variant,
        )
        published = self._cache_root / adapter.resource_kind / key
        lock_path = self._cache_root / '.locks' / f'{adapter.resource_kind}-{key}.lock'
        try:
            async with _exclusive_lock(lock_path, timeout_seconds=self._lock_timeout_seconds, io=self._io):
                cached = _read_receipt(published / '.materialization-receipt.json', io=self._io)
                if (
                    cached is not None
                    and _receipt_matches_request(cached, adapter=adapter, resolved_revision=resolved_revision)
                    and await adapter.verify_cache(published, cached)
                ):
                    cached = replace(cached, cache_status='verified_hit')
                    receipt_ref = _write_receipt(
                        published,
                        cached,
                        max_receipt_chars=self._max_receipt_chars,
                        io=self._io,
                    )
                    return MaterializationResult(True, published, cached, receipt_ref)
                self._io.read(published)
                staging = self._private_staging(adapter.resource_kind, key)
                try:
                    details = await adapter.populate(staging, resolved_revision)
                    receipt = MaterializationReceipt(
                        resource_kind=adapter.resource_kind,
                        resource_identity=adapter.resource_identity,
                        requested_revision=adapter.requested_revision,
                        resolved_revision=resolved_revision,
                        route_class=str(details.get('route_class', adapter.route_class)),
                        verified_files=int(details.get('verified_files', 0)),
                        verified_bytes=int(details.get('verified_bytes', 0)),
                        cache_status='published',
                        manifest_ref='.materialization-manifest.json',
                        warnings=tuple(str(item) for item in details.get('warnings', ()))[:8],
                    )
                    if receipt.verified_files <= 0 or receipt.verified_bytes < 0:
                        raise MaterializationFailure(
                            'materialized resource produced an empty or invalid payload',
                            owner='runtime_capability_missing',
                            code='empty_materialized_resource',
                        )
                    _write_json(staging / receipt.manifest_ref, dict(details.get('manifest', {})), io=self._io)
                    _write_receipt(
                        staging,
                        receipt,
                        max_receipt_chars=self._max_receipt_chars,
                        io=self._io,
                    )
                    self._io.mkdir(published.parent)
                    self._io.check_replace(staging, published)
                    if published.exists():
                        _quarantine(published, io=self._io)
                    self._io.replace(staging, published)
                    _fsync_dir(published.parent, io=self._io)
                    if not await adapter.verify_cache(published, receipt):
                        _quarantine(published, io=self._io)
                        raise MaterializationFailure(
                            'published resource failed readback verification',
                            owner='runtime_capability_missing',
                            code='published_readback_failed',
                        )
                    return MaterializationResult(
                        True,
                        published,
                        receipt,
                        published / '.materialization-receipt.json',
                    )
                finally:
                    self._io.remove_tree(staging)
        except MaterializationFailure as exc:
            return MaterializationResult(
                False,
                error=str(exc),
                failure_owner=exc.owner,
                failure_code=exc.code,
            )
        except PermissionError:
            raise
        except Exception as exc:
            return MaterializationResult(
                False,
                error=str(exc)[:1000],
                failure_owner='runtime_capability_missing',
                failure_code='materialization_internal_error',
            )

    def _private_staging(self, kind: str, key: str) -> Path:
        root = self._cache_root / '.partial'
        self._io.mkdir(root)
        staging = root / f'{kind}-{key}-{os.getpid()}-{time.time_ns()}'
        self._io.mkdir(staging, mode=0o700, exist_ok=False)
        return staging

    def _private_resolution(self, kind: str) -> Path:
        root = self._cache_root / '.resolve'
        self._io.mkdir(root)
        staging = root / f'{kind}-{os.getpid()}-{time.time_ns()}'
        self._io.mkdir(staging, mode=0o700, exist_ok=False)
        return staging


def verify_manifest_files(
    root: Path,
    manifest: dict[str, object],
    *,
    expected_files: int | None = None,
    expected_bytes: int | None = None,
    ignored_top_level: tuple[str, ...] = (),
    io: MaterializationFileIO | None = None,
) -> bool:
    io = io or MaterializationFileIO()
    files = manifest.get('files', [])
    if not isinstance(files, list) or not files:
        return False
    if expected_files is not None and len(files) != expected_files:
        return False
    verified_bytes = 0
    expected_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            return False
        relative = str(item.get('path', ''))
        candidate = _safe_relative(root, relative)
        io.read(candidate)
        if relative in expected_paths:
            return False
        expected_paths.add(relative)
        expected_size = int(item.get('size', -1))
        if not candidate.is_file() or candidate.stat().st_size != expected_size:
            return False
        verified_bytes += expected_size
        digest = str(item.get('sha256', ''))
        if digest and _sha256(candidate, io=io) != digest:
            return False
    if expected_bytes is not None and verified_bytes != expected_bytes:
        return False
    actual_paths: set[str] = set()
    for candidate in io.walk(root):
        if candidate.is_symlink():
            return False
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if len(relative.parts) == 1 and relative.name in {
            '.materialization-manifest.json',
            '.materialization-receipt.json',
        }:
            continue
        if relative.parts and relative.parts[0] in ignored_top_level:
            continue
        actual_paths.add(relative.as_posix())
    return actual_paths == expected_paths


@asynccontextmanager
async def _exclusive_lock(path: Path, *, timeout_seconds: int, io: MaterializationFileIO) -> AsyncIterator[None]:
    io.mkdir(path.parent)
    io.write(path)
    with path.open('a+', encoding='utf-8') as handle:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise MaterializationFailure(
                        'resource lock timed out',
                        owner='runtime_capability_missing',
                        code='lock_timeout',
                    )
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _resource_key(kind: str, identity: str, revision: str, variant: str) -> str:
    payload = f'{kind}\0{identity}\0{revision}\0{variant}'.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()[:24]


def _safe_relative(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not relative:
        raise MaterializationFailure(
            f'unsafe resource path: {relative}',
            owner='runtime_capability_missing',
            code='unsafe_resource_path',
        )
    candidate = root / path
    if candidate.is_symlink():
        raise MaterializationFailure(
            f'symlink resource path is not allowed: {relative}',
            owner='runtime_capability_missing',
            code='unsafe_resource_path',
        )
    return candidate


def _write_receipt(
    root: Path,
    receipt: MaterializationReceipt,
    *,
    max_receipt_chars: int,
    io: MaterializationFileIO,
) -> Path:
    path = root / '.materialization-receipt.json'
    _write_json(path, receipt.to_dict(), io=io)
    io.read(path)
    if path.stat().st_size > max_receipt_chars:
        raise MaterializationFailure(
            'materialization receipt exceeded bounded size',
            owner='runtime_capability_missing',
            code='receipt_too_large',
        )
    return path


def _write_json(path: Path, payload: dict[str, object], *, io: MaterializationFileIO) -> None:
    io.write(path)
    io.mkdir(path.parent)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    io.write(temp)
    with temp.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())
    io.replace(temp, path)
    _fsync_dir(path.parent, io=io)


def _read_receipt(path: Path, *, io: MaterializationFileIO) -> MaterializationReceipt | None:
    try:
        payload = json.loads(io.read_text(path))
        return MaterializationReceipt(
            resource_kind=str(payload['resource_kind']),
            resource_identity=str(payload['resource_identity']),
            requested_revision=str(payload['requested_revision']),
            resolved_revision=str(payload['resolved_revision']),
            route_class=str(payload['route_class']),
            verified_files=int(payload['verified_files']),
            verified_bytes=int(payload['verified_bytes']),
            cache_status=str(payload['cache_status']),
            manifest_ref=str(payload['manifest_ref']),
            warnings=tuple(str(item) for item in payload.get('warnings', [])),
        )
    except PermissionError:
        raise
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _receipt_matches_request(
    receipt: MaterializationReceipt,
    *,
    adapter: MaterializationAdapter,
    resolved_revision: str,
) -> bool:
    return (
        receipt.resource_kind == adapter.resource_kind
        and receipt.resource_identity == adapter.resource_identity
        and receipt.requested_revision == adapter.requested_revision
        and receipt.resolved_revision == resolved_revision
        and receipt.route_class == adapter.route_class
        and receipt.manifest_ref == '.materialization-manifest.json'
        and receipt.verified_files > 0
        and receipt.verified_bytes >= 0
    )


def _quarantine(path: Path, *, io: MaterializationFileIO) -> None:
    target = path.with_name(f'{path.name}.corrupt-{time.time_ns()}')
    io.replace(path, target)
    io.remove_tree(target)


def _sha256(path: Path, *, io: MaterializationFileIO) -> str:
    io.read(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path, *, io: MaterializationFileIO) -> None:
    io.read(path)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
