from ownpi.runtime.canonical_documents.models import (
    CanonicalTextPolicy,
    PreparedCanonicalText,
)
from ownpi.runtime.canonical_documents.submission import (
    CanonicalDocumentError,
    prepare_canonical_text,
)


EXACT_TEXT_POLICY = CanonicalTextPolicy()
STRIPPED_TEXT_POLICY = CanonicalTextPolicy(strip_outer_whitespace=True)
STRIPPED_FINAL_NEWLINE_POLICY = CanonicalTextPolicy(
    strip_outer_whitespace=True,
    final_newline=True,
)


__all__ = [
    "CanonicalDocumentError",
    "CanonicalTextPolicy",
    "EXACT_TEXT_POLICY",
    "PreparedCanonicalText",
    "STRIPPED_FINAL_NEWLINE_POLICY",
    "STRIPPED_TEXT_POLICY",
    "prepare_canonical_text",
]
