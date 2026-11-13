from __future__ import annotations

from dataclasses import dataclass


class InstructionResolutionError(RuntimeError):
    """Raised when the canonical instruction graph cannot be resolved safely."""


@dataclass(frozen=True)
class ResolvedInstruction:
    source_family: str
    scope: str
    locator: str
    relative_path: str
    digest: str
    content: str
    order_key: int
    included_from: str = ""

    @property
    def char_count(self) -> int:
        return len(self.content)

    def to_metadata(self) -> dict[str, object]:
        return {
            "source_family": self.source_family,
            "scope": self.scope,
            "locator": self.locator,
            "relative_path": self.relative_path,
            "digest": self.digest,
            "char_count": self.char_count,
            "order_key": self.order_key,
            "included_from": self.included_from,
        }
