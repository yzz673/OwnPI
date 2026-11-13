from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ownpi.controller.interaction_actions import CancelLocalCapture, InteractionAction, RespondPermission, SubmitHumanAnswers


@dataclass(frozen=True)
class InteractionActionResult:
    status: str
    message: str = ''
    payload: object | None = None

    @property
    def cancelled_local(self) -> bool:
        return self.status == 'cancelled_local'


class InteractionController:
    def __init__(self, *, engine: object) -> None:
        self._engine = engine

    def apply_action(self, run_dir: Path, action: InteractionAction) -> InteractionActionResult:
        run_dir = run_dir.expanduser().resolve()
        if isinstance(action, CancelLocalCapture):
            return InteractionActionResult(
                status='cancelled_local',
                message='Local capture cancelled. Run remains paused_for_human.',
            )
        if isinstance(action, SubmitHumanAnswers):
            payload = self._engine.answer_pending_interaction(
                run_dir,
                answers=action.answers_by_question_id,
                session_id=action.interaction_id or None,
            )
            return InteractionActionResult(status='submitted', payload=payload)
        if isinstance(action, RespondPermission):
            payload = self._engine.answer_tool_permission(
                run_dir,
                decision=action.decision,
                scope=action.scope,
                session_id=action.interaction_id,
            )
            return InteractionActionResult(status='submitted', payload=payload)
        raise TypeError(f'Unsupported interaction action: {type(action)!r}')
