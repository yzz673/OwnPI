from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


_JSONL_MAX_LINE_BYTES = 1_000_000


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize(data: Any) -> Any:
    if is_dataclass(data):
        return _normalize(asdict(data))
    if isinstance(data, Path):
        return str(data)
    if isinstance(data, (datetime, date)):
        return data.isoformat()
    if isinstance(data, dict):
        return {str(key): _normalize(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_normalize(item) for item in data]
    if isinstance(data, tuple):
        return [_normalize(item) for item in data]
    if isinstance(data, set):
        return [_normalize(item) for item in sorted(data, key=str)]
    return data


def fsync_parent_dir(path: Path) -> None:
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_yaml(path: Path, data: Any) -> None:
    normalized = _normalize(data)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(normalized, file, sort_keys=False, allow_unicode=True)


def write_json(path: Path, data: Any) -> None:
    normalized = _normalize(data)
    content = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    write_bytes_atomic(path, content.encode("utf-8"))


def write_text_atomic(path: Path, content: str) -> None:
    """Write UTF-8 text with the same durability guarantees as ``write_json``."""
    write_bytes_atomic(path, str(content).encode("utf-8"))


def write_bytes_atomic(path: Path, content: bytes) -> None:
    """Replace a file atomically after durable file and parent-directory fsyncs."""
    ensure_dir(path.parent)
    existing_mode = _existing_file_mode(path)
    temporary_path, descriptor = _open_unique_temporary_file(path)
    try:
        if existing_mode is not None:
            os.fchmod(descriptor, existing_mode)
        with os.fdopen(descriptor, "wb") as file:
            descriptor = -1
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        fsync_parent_dir(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _existing_file_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _open_unique_temporary_file(path: Path) -> tuple[Path, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for _ in range(100):
        temporary_path = path.parent / f'.{path.name}.{secrets.token_hex(8)}.tmp'
        try:
            descriptor = os.open(temporary_path, flags, 0o666)
        except FileExistsError:
            continue
        return temporary_path, descriptor
    raise FileExistsError(f'unable to create unique temporary file for {path}')


def append_jsonl(path: Path, data: Any) -> None:
    normalized = _normalize(data)
    encoded = serialize_jsonl_line(normalized)
    encoded = bounded_jsonl_line(normalized, encoded)
    _append_jsonl_bytes(path, encoded)


def serialize_jsonl_line(data: Any) -> bytes:
    normalized = _normalize(data)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode('utf-8')


def bounded_jsonl_line(data: Any, encoded: bytes) -> bytes:
    if len(encoded) > _JSONL_MAX_LINE_BYTES:
        normalized = _normalize(data)
        fallback = _jsonl_budget_fallback(normalized, line_bytes=len(encoded))
        return serialize_jsonl_line(fallback)
    return encoded


def _append_jsonl_bytes(path: Path, encoded: bytes) -> None:
    ensure_dir(path.parent)
    with path.open("ab") as file:
        file.write(encoded)
        file.write(b"\n")
        file.flush()
        os.fsync(file.fileno())
    fsync_parent_dir(path)


def _jsonl_budget_fallback(data: Any, *, line_bytes: int) -> Any:
    if not isinstance(data, dict):
        return {
            'truncated': True,
            'truncation_reason': 'jsonl_line_byte_budget_exceeded',
            'original_line_bytes': line_bytes,
            'payload_type': type(data).__name__,
        }
    preserved_keys = (
        'event_id',
        'created_at',
        'trace_id',
        'component',
        'event_type',
        'session_id',
        'task_id',
        'run_id',
        'stage',
        'attempt',
        'continuation_index',
        'turn_index',
        'operation',
        'status',
        'channel',
    )
    fallback = {key: data.get(key) for key in preserved_keys if key in data}
    payload = data.get('payload')
    fallback['payload'] = {
        'truncated': True,
        'truncation_reason': 'jsonl_line_byte_budget_exceeded',
        'original_line_bytes': line_bytes,
        'payload_keys': sorted(str(key) for key in payload.keys()) if isinstance(payload, dict) else [],
    }
    return fallback
