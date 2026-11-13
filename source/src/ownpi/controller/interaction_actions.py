from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class SubmitHumanAnswers:
    interaction_id: str
    answers_by_question_id: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class RespondPermission:
    interaction_id: str
    decision: Literal['approve', 'deny', 'deny_command']
    scope: Literal['once', 'session'] = 'once'


@dataclass(frozen=True)
class CancelLocalCapture:
    interaction_id: str


InteractionAction: TypeAlias = SubmitHumanAnswers | RespondPermission | CancelLocalCapture
