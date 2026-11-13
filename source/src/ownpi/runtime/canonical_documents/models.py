from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalTextPolicy:
    strip_outer_whitespace: bool = False
    final_newline: bool = False


@dataclass(frozen=True)
class PreparedCanonicalText:
    logical_content: str
    serialized_text: str
    content_sha256: str
    content_chars: int


__all__ = [
    "CanonicalTextPolicy",
    "PreparedCanonicalText",
]
