from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ownpi.schemas.runtime import LLMConfig


APPROX_BYTES_PER_TOKEN = 4
DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT = 10_000
TruncationMode = Literal["bytes", "tokens"]


@dataclass(frozen=True)
class TruncationPolicy:
    mode: TruncationMode
    limit: int

    def __post_init__(self) -> None:
        if self.mode not in {"bytes", "tokens"}:
            raise ValueError(f"unsupported truncation mode: {self.mode}")
        if self.limit < 0:
            raise ValueError("truncation limit must be non-negative")

    @classmethod
    def bytes(cls, limit: int) -> TruncationPolicy:
        return cls(mode="bytes", limit=int(limit))

    @classmethod
    def tokens(cls, limit: int) -> TruncationPolicy:
        return cls(mode="tokens", limit=int(limit))

    @property
    def byte_budget(self) -> int:
        if self.mode == "bytes":
            return self.limit
        return self.limit * APPROX_BYTES_PER_TOKEN

    @property
    def token_budget(self) -> int:
        if self.mode == "tokens":
            return self.limit
        return approx_tokens_from_byte_count(self.limit)

    def scaled(self, multiplier: float) -> TruncationPolicy:
        if not math.isfinite(multiplier) or multiplier < 0:
            raise ValueError("truncation multiplier must be finite and non-negative")
        scaled_limit = Decimal(self.limit) * Decimal(str(multiplier))
        return TruncationPolicy(
            mode=self.mode,
            limit=int(scaled_limit.to_integral_value(rounding=ROUND_CEILING)),
        )


DEFAULT_TOOL_RESULT_TRUNCATION_POLICY = TruncationPolicy.bytes(
    DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT
)

_TOKEN_POLICY_MODELS = frozenset(
    {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-daybreak-blue-latest",
        "gpt-daybreak-red-latest",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "codex-auto-review",
    }
)


def resolve_tool_result_truncation_policy(
    llm_config: LLMConfig | None,
) -> TruncationPolicy:
    model = str(getattr(llm_config, "model", "") or "").strip().casefold()
    if model in _TOKEN_POLICY_MODELS:
        return TruncationPolicy.tokens(DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT)
    return DEFAULT_TOOL_RESULT_TRUNCATION_POLICY


def approx_token_count(text: str) -> int:
    return approx_tokens_from_byte_count(len(text.encode("utf-8")))


def approx_tokens_from_byte_count(byte_count: int) -> int:
    normalized = max(0, int(byte_count))
    return (normalized + APPROX_BYTES_PER_TOKEN - 1) // APPROX_BYTES_PER_TOKEN


def truncate_text(content: str, policy: TruncationPolicy) -> str:
    source = str(content or "")
    if not source:
        return ""
    max_bytes = policy.byte_budget
    if max_bytes > 0 and len(source.encode("utf-8")) <= max_bytes:
        return source
    return _truncate_with_byte_estimate(
        source,
        max_bytes=max_bytes,
        use_tokens=policy.mode == "tokens",
    )


def _truncate_with_byte_estimate(
    source: str,
    *,
    max_bytes: int,
    use_tokens: bool,
) -> str:
    source_bytes = source.encode("utf-8")
    total_chars = len(source)
    if max_bytes == 0:
        return _truncation_marker(
            use_tokens=use_tokens,
            removed_count=(
                approx_tokens_from_byte_count(len(source_bytes))
                if use_tokens
                else total_chars
            ),
        )
    if len(source_bytes) <= max_bytes:
        return source

    left_budget = max_bytes // 2
    right_budget = max_bytes - left_budget
    prefix = source_bytes[:left_budget].decode("utf-8", errors="ignore")
    suffix = source_bytes[len(source_bytes) - right_budget :].decode(
        "utf-8",
        errors="ignore",
    )
    removed_chars = total_chars - len(prefix) - len(suffix)
    removed_count = (
        approx_tokens_from_byte_count(len(source_bytes) - max_bytes)
        if use_tokens
        else removed_chars
    )
    return f"{prefix}{_truncation_marker(use_tokens=use_tokens, removed_count=removed_count)}{suffix}"


def _truncation_marker(*, use_tokens: bool, removed_count: int) -> str:
    unit = "tokens" if use_tokens else "chars"
    return f"…{max(0, removed_count)} {unit} truncated…"


__all__ = [
    "APPROX_BYTES_PER_TOKEN",
    "DEFAULT_TOOL_RESULT_TRUNCATION_POLICY",
    "DEFAULT_TOOL_RESULT_TRUNCATION_POLICY_LIMIT",
    "TruncationPolicy",
    "approx_token_count",
    "approx_tokens_from_byte_count",
    "resolve_tool_result_truncation_policy",
    "truncate_text",
]
