from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    path: str
    source: str
    tags: tuple[str, ...] = ()
    priority: int = 0
    when_to_use: str | None = None
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AgentContextTrace:
    agent_id: str
    role: str
    selected_at_stage: str
    rationale: str
    available_skill_count: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentContextTrace":
        return cls(
            agent_id=str(data.get("agent_id", "")),
            role=str(data.get("role", "")),
            selected_at_stage=str(data.get("selected_at_stage", "")),
            rationale=str(data.get("rationale", "")),
            available_skill_count=int(data.get("available_skill_count", 0)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "selected_at_stage": self.selected_at_stage,
            "rationale": self.rationale,
            "available_skill_count": self.available_skill_count,
        }

    def logical_stage(self) -> str:
        return _logical_stage_name(self.selected_at_stage)

    def compact_summary(self) -> str:
        return f"{self.agent_id}@{self.selected_at_stage} catalog={self.available_skill_count}s"

    def inline_summary(self, *, max_rationale_chars: int = 48) -> str:
        prefix = f"{self.agent_id}@{self.logical_stage()}"
        parts = [
            prefix,
            f"avail={self.available_skill_count}s",
            "catalog-only",
        ]
        rationale = _compact_text(self.rationale, max_chars=max_rationale_chars)
        if rationale:
            parts.append(f"why={rationale}")
        return " ".join(parts)

    def summary_dict(self) -> dict[str, object]:
        fingerprint = f"catalog={self.available_skill_count}"
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "selected_at_stage": self.selected_at_stage,
            "logical_stage": self.logical_stage(),
            "available_skill_count": self.available_skill_count,
            "compact_summary": self.compact_summary(),
            "inline_summary": self.inline_summary(),
            "catalog_fingerprint": fingerprint,
            "event_fingerprint": f"{self.logical_stage()}|{fingerprint}",
        }


@dataclass(frozen=True)
class ToolSelectionTrace:
    agent_id: str
    role: str
    selected_at_stage: str
    available_tool_count: int
    loaded_tool_count: int
    selection_summary: str
    available_tool_names: list[str] = field(default_factory=list)
    loaded_tool_names: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolSelectionTrace":
        return cls(
            agent_id=str(data.get("agent_id", "")),
            role=str(data.get("role", "")),
            selected_at_stage=str(data.get("selected_at_stage", "")),
            available_tool_count=int(data.get("available_tool_count", 0)),
            loaded_tool_count=int(data.get("loaded_tool_count", 0)),
            selection_summary=str(data.get("selection_summary", "")),
            available_tool_names=[str(item) for item in data.get("available_tool_names", [])],
            loaded_tool_names=[str(item) for item in data.get("loaded_tool_names", [])],
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def logical_stage(self) -> str:
        return _logical_stage_name(self.selected_at_stage)

    def selection_fingerprint(self) -> str:
        return f"available={'|'.join(self.available_tool_names)};loaded={'|'.join(self.loaded_tool_names)}"

    def compact_summary(self) -> str:
        return (
            f"{self.agent_id}@{self.selected_at_stage} "
            f"tools={self.loaded_tool_count}/{self.available_tool_count}"
            f"{_compact_names(self.loaded_tool_names, max_items=2)}"
        )

    def inline_summary(self, *, max_items: int = 2) -> str:
        return " ".join(
            [
                f"{self.agent_id}@{self.logical_stage()}",
                f"avail={len(self.available_tool_names)}{_compact_names(self.available_tool_names, max_items=max_items)}",
                f"load={len(self.loaded_tool_names)}{_compact_names(self.loaded_tool_names, max_items=max_items)}",
            ]
        )

    def summary_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "selected_at_stage": self.selected_at_stage,
            "logical_stage": self.logical_stage(),
            "available_tool_count": self.available_tool_count,
            "loaded_tool_count": self.loaded_tool_count,
            "available_tool_names": list(self.available_tool_names),
            "loaded_tool_names": list(self.loaded_tool_names),
            "selection_summary": self.selection_summary,
            "compact_summary": self.compact_summary(),
            "inline_summary": self.inline_summary(),
            "selection_fingerprint": self.selection_fingerprint(),
            "event_fingerprint": f"{self.logical_stage()}|{self.selection_fingerprint()}",
        }


def summarize_context_runtime(runtime_trace: dict[str, Any], *, max_items: int = 2) -> dict[str, object]:
    raw_agent_runs = runtime_trace.get("agent_runs", [])
    if not isinstance(raw_agent_runs, list):
        return {"agent_run_count": 0, "latest_by_agent": []}
    latest_by_agent: dict[str, dict[str, object]] = {}
    ordered_agent_ids: list[str] = []
    for item in raw_agent_runs:
        if not isinstance(item, dict):
            continue
        trace = AgentContextTrace.from_dict(item)
        summary = trace.summary_dict()
        latest_by_agent[trace.agent_id] = summary
        if trace.agent_id in ordered_agent_ids:
            ordered_agent_ids.remove(trace.agent_id)
        ordered_agent_ids.append(trace.agent_id)
    return {
        "agent_run_count": len(raw_agent_runs),
        "latest_by_agent": [latest_by_agent[agent_id] for agent_id in ordered_agent_ids],
    }


def summarize_tool_runtime(runtime_trace: dict[str, Any]) -> dict[str, object]:
    raw_tool_runs = runtime_trace.get("tool_runs", [])
    if not isinstance(raw_tool_runs, list):
        return {"tool_run_count": 0, "latest_by_agent": []}
    latest_by_agent: dict[str, dict[str, object]] = {}
    ordered_agent_ids: list[str] = []
    for item in raw_tool_runs:
        if not isinstance(item, dict):
            continue
        trace = ToolSelectionTrace.from_dict(item)
        summary = trace.summary_dict()
        latest_by_agent[trace.agent_id] = summary
        if trace.agent_id in ordered_agent_ids:
            ordered_agent_ids.remove(trace.agent_id)
        ordered_agent_ids.append(trace.agent_id)
    return {
        "tool_run_count": len(raw_tool_runs),
        "latest_by_agent": [latest_by_agent[agent_id] for agent_id in ordered_agent_ids],
    }


def _compact_names(names: list[str], *, max_items: int) -> str:
    if not names:
        return "[]"
    unique_names = list(dict.fromkeys(names))
    preview = [_compact_token(name) for name in unique_names[:max_items]]
    if len(unique_names) > max_items:
        preview.append(f"+{len(unique_names) - max_items}")
    return "[" + ",".join(preview) + "]"


def _logical_stage_name(selected_at_stage: str) -> str:
    for suffix in ("_reviewer_review",):
        if selected_at_stage.endswith(suffix):
            return selected_at_stage[: -len(suffix)]
    return selected_at_stage


def _compact_text(text: str, *, max_chars: int) -> str:
    normalized = " ".join(str(text).split())
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _compact_token(text: str, *, max_chars: int = 24) -> str:
    return _compact_text(text, max_chars=max_chars)
