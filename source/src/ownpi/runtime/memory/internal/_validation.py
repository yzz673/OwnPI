from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping


_REF_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*"
        r"[^\s,;]{8,}",
        re.IGNORECASE,
    ),
)


def bounded_text(
    value: object,
    *,
    field_name: str,
    max_length: int | None = None,
    required: bool = True,
    reject_secrets: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if required and not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{field_name} cannot contain control characters")
    if reject_secrets and any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise ValueError(f"{field_name} contains credential-like material")
    return value


def bounded_ref_segment(value: object, *, field_name: str, max_length: int) -> str:
    text = bounded_text(value, field_name=field_name, max_length=max_length)
    if _REF_SEGMENT_PATTERN.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a canonical opaque segment")
    return text


def require_enum(value: object, enum_type: type[Enum], *, field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field_name} must use {enum_type.__name__}")


def require_tuple(
    values: object,
    *,
    field_name: str,
    max_items: int | None = None,
) -> tuple[object, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must use an immutable tuple")
    if max_items is not None and len(values) > max_items:
        raise ValueError(f"{field_name} exceeds maximum item count")
    return values


def require_unique(values: tuple[object, ...], *, field_name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")


def bounded_positive_int(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}")
    return value


def bounded_count(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return value


def validate_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def parse_utc_timestamp(value: object, *, field_name: str) -> datetime:
    text = bounded_text(value, field_name=field_name, max_length=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must use UTC")
    return parsed


def require_exact_keys(
    payload: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"serialized {label} must be an object")
    keys = set(payload)
    missing = required - keys
    unknown = keys - required - optional
    if missing or unknown:
        raise ValueError(
            f"serialized {label} has invalid fields: "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    return payload
