from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ownpi.runtime.contracts import AgentOutput


@dataclass(frozen=True)
class AgentStreamEvent:
    event_type: str
    operation: str = ''
    stage: str = ''
    delta: str = ''
    payload: dict[str, Any] = field(default_factory=dict)
    agent_output: AgentOutput | None = None


@dataclass
class TextDeltaReceipt:
    char_count: int = 0
    _digest: Any = field(default_factory=hashlib.sha256, repr=False)

    def append(self, delta: str) -> None:
        self.char_count += len(delta)
        self._digest.update(delta.encode('utf-8'))

    def completed_suffix(self, completed_text: str) -> str | None:
        if self.char_count > len(completed_text):
            return None
        completed_prefix = completed_text[: self.char_count]
        completed_digest = hashlib.sha256(completed_prefix.encode('utf-8')).hexdigest()
        if completed_digest != self._digest.hexdigest():
            return None
        return completed_text[self.char_count :]
