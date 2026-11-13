from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from ownpi.schemas.workflow_plan import ExecutionTier, StageRuntimeProfile, execution_tier_for_profile
from ownpi.controller.run_context import PreparedRunContext
from ownpi.runtime.hooks.types import HookModelContextFragment
from ownpi.task_authoring.contracts import TaskSpecAuthoringOutcome


@dataclass(frozen=True)
class NewMessageAdmission:
    request_id: str
    raw_user_message: str
    stage_runtime_profile: StageRuntimeProfile
    run_context: PreparedRunContext
    task_spec_authoring: TaskSpecAuthoringOutcome | None = None
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = ()
    session_start_hook_emitted: bool = False

    @property
    def is_direct(self) -> bool:
        return execution_tier_for_profile(self.stage_runtime_profile) is ExecutionTier.DIRECT


def select_new_message_admission(
    *,
    raw_user_message: str,
    stage_runtime_profile: StageRuntimeProfile,
    run_context: PreparedRunContext,
    hook_prefix_fragments: tuple[HookModelContextFragment, ...] = (),
    session_start_hook_emitted: bool = False,
) -> NewMessageAdmission:
    request_id = f'admission-{uuid4().hex}'
    return NewMessageAdmission(
        request_id=request_id,
        raw_user_message=raw_user_message,
        stage_runtime_profile=stage_runtime_profile,
        run_context=run_context,
        hook_prefix_fragments=hook_prefix_fragments,
        session_start_hook_emitted=session_start_hook_emitted,
    )
