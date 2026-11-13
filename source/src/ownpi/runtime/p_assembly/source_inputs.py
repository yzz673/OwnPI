"""Canonical owner inputs used by P source access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ownpi.runtime.contracts import (
    ArtifactRecord,
)
from ownpi.runtime.memory.map_contracts import MemoryMap
from ownpi.runtime.hooks.context_delivery import select_active_hook_fragments
from ownpi.runtime.hooks.types import HookDeliveryTarget, HookModelContextFragment
from ownpi.runtime.state_models import FeedbackState, RevisitState, RuntimeState
from ownpi.runtime.task_context import RuntimeTaskContext
from ownpi.runtime.tools.router import ToolRouter
from ownpi.skills.snapshot import SkillCatalogSnapshot


@dataclass(frozen=True)
class PSourceInputs:
    """Typed handles to existing canonical source owners."""

    task_context: RuntimeTaskContext | None = None
    task_message: str = ""
    run_dir: Path | None = None
    core_submission: str = ""
    core_submission_identity: str = ""
    direct_request_context: str = ""
    direct_request_context_identity: str = ""
    direct_review_evidence: str = ""
    direct_review_evidence_identity: str = ""
    review_context: str = ""
    review_context_identity: str = ""
    submitted_answer: str = ""
    submitted_answer_identity: str = ""
    subagent_enabled: bool = False
    subagent_max_count: int = 8
    skill_catalog_snapshot: SkillCatalogSnapshot | None = None
    tool_router: ToolRouter | None = None
    memory_map: MemoryMap | None = None
    task_image_records: tuple[ArtifactRecord, ...] = ()
    revisit_state: RevisitState | None = None
    feedback_state: FeedbackState | None = None
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = ()

    def __post_init__(self) -> None:
        tuple_fields = (
            self.task_image_records,
            self.hook_prefix_fragments,
        )
        if any(not isinstance(value, tuple) for value in tuple_fields):
            raise TypeError("P source input collections must be immutable tuples")
        if not isinstance(self.task_message, str):
            raise TypeError("task_message must be a string")

    @classmethod
    def from_runtime_state(
        cls,
        state: RuntimeState,
        *,
        skill_catalog_snapshot: SkillCatalogSnapshot | None = None,
        tool_router: ToolRouter | None = None,
        memory_map: MemoryMap | None = None,
        runtime_config: object | None = None,
    ) -> "PSourceInputs":
        """Take one immutable snapshot of canonical runtime-owned P sources."""
        artifact_state = state.artifact_state
        workflow_state = state.workflow_state
        metadata = state.run_metadata
        return cls(
            subagent_enabled=bool(getattr(runtime_config, "subagent_enabled", False)),
            subagent_max_count=getattr(runtime_config, "subagent_max_count", 8),
            task_context=state.task_context,
            skill_catalog_snapshot=skill_catalog_snapshot,
            tool_router=tool_router,
            memory_map=memory_map,
            task_image_records=tuple(artifact_state.recent_artifacts),
            revisit_state=workflow_state.revisit_state,
            feedback_state=state.feedback_state,
            hook_prefix_fragments=select_active_hook_fragments(
                state.context_state,
                target=HookDeliveryTarget.IMMUTABLE_PREFIX,
                session_id=str(metadata.session_id or ""),
                run_id=str(metadata.run_id or ""),
                stage=(
                    workflow_state.current_stage.value
                    if workflow_state.current_stage is not None
                    else ""
                ),
                attempt=workflow_state.current_stage_attempt,
            ),
        )


__all__ = ["PSourceInputs"]
