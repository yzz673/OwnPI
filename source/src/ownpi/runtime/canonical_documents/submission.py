from __future__ import annotations

from hashlib import sha256

from ownpi.runtime.canonical_documents.models import (
    CanonicalTextPolicy,
    PreparedCanonicalText,
)


class CanonicalDocumentError(ValueError):
    pass


def prepare_canonical_text(
    content: str,
    *,
    policy: CanonicalTextPolicy,
) -> PreparedCanonicalText:
    if not isinstance(content, str):
        raise TypeError("canonical document content must be a string")
    logical_content = content.strip() if policy.strip_outer_whitespace else content
    if not logical_content.strip():
        raise CanonicalDocumentError("canonical document content must not be empty")
    serialized_text = logical_content
    if policy.final_newline:
        serialized_text = f"{logical_content.rstrip()}\n"
    return PreparedCanonicalText(
        logical_content=logical_content,
        serialized_text=serialized_text,
        content_sha256=sha256(logical_content.encode("utf-8")).hexdigest(),
        content_chars=len(logical_content),
    )


__all__ = [
    "CanonicalDocumentError",
    "prepare_canonical_text",
]
