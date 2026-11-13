from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ContinuationExecutionKind = Literal["stage_continuation"]


@dataclass(frozen=True)
class RuntimeContinuationDirective:
    execution_kind: ContinuationExecutionKind
    source_record_id: str
    stage: str
    stage_attempt: int
    continuation_index: int
    query_chain_id: str
    query_chain_depth: int

    def __post_init__(self) -> None:
        if self.execution_kind != "stage_continuation":
            raise ValueError(f"Unsupported continuation execution kind: {self.execution_kind!r}")
        if not self.source_record_id.strip():
            raise ValueError("Runtime continuation requires a source Record ID.")
        if not self.stage.strip() or self.stage_attempt <= 0:
            raise ValueError("Runtime continuation requires a positive stage attempt.")
        if self.continuation_index < 0 or self.query_chain_depth < 0:
            raise ValueError("Runtime continuation indices cannot be negative.")
        if self.query_chain_depth > 0 and not self.query_chain_id.strip():
            raise ValueError("A positive query-chain depth requires a query-chain ID.")

    def summary(self) -> dict[str, str | int]:
        return {
            "execution_kind": self.execution_kind,
            "source_record_id": self.source_record_id[:160],
            "stage": self.stage[:160],
            "stage_attempt": self.stage_attempt,
            "continuation_index": self.continuation_index,
            "query_chain_id": self.query_chain_id[:160],
            "query_chain_depth": self.query_chain_depth,
        }

    def matches_stage_attempt(self, *, stage: str, stage_attempt: int) -> bool:
        return self.stage == stage and self.stage_attempt == stage_attempt
