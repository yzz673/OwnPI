"""Single source for workflow-level revisit limits."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ownpi.schemas.workflow_plan import StageRuntimeProfile


AUTO_REVIEW_PROFILE = "auto"


class ReviewProfile(StrEnum):
    STANDARD = "standard"
    RIGOROUS = "rigorous"
    EXHAUSTIVE = "exhaustive"


@dataclass(frozen=True)
class EffectiveReviewPolicy:
    max_total_revisits: int | None
    selected_profile: ReviewProfile | None = None

    def __post_init__(self) -> None:
        value = self.max_total_revisits
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError("max_total_revisits must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_profile": (
                self.selected_profile.value if self.selected_profile is not None else None
            ),
            "max_total_revisits": self.max_total_revisits,
        }


_WORKFLOW_CONTROL_LIMITS_BY_PROFILE: dict[ReviewProfile, int | None] = {
    ReviewProfile.STANDARD: 1,
    ReviewProfile.RIGOROUS: 3,
    ReviewProfile.EXHAUSTIVE: None,
}


def review_profile_from_value(value: Any) -> ReviewProfile:
    if isinstance(value, ReviewProfile):
        return value
    if not isinstance(value, str):
        raise ValueError("runtime.review_profile must be standard, rigorous, or exhaustive")
    try:
        return ReviewProfile(value.strip().lower())
    except ValueError as exc:
        raise ValueError(
            "runtime.review_profile must be standard, rigorous, or exhaustive"
        ) from exc


def review_profile_selection_from_value(value: Any) -> str:
    if isinstance(value, ReviewProfile):
        return value.value
    if not isinstance(value, str):
        raise ValueError(
            "runtime.review_profile must be auto, standard, rigorous, or exhaustive"
        )
    normalized = value.strip().lower()
    if normalized == AUTO_REVIEW_PROFILE:
        return normalized
    try:
        return ReviewProfile(normalized).value
    except ValueError as exc:
        raise ValueError(
            "runtime.review_profile must be auto, standard, rigorous, or exhaustive"
        ) from exc


def default_review_profile_for_level(
    stage_runtime_profile: StageRuntimeProfile | str,
) -> ReviewProfile:
    profile = StageRuntimeProfile(stage_runtime_profile)
    if profile is StageRuntimeProfile.RESEARCH_LEVEL:
        return ReviewProfile.RIGOROUS
    return ReviewProfile.STANDARD


def resolve_review_policy(
    value: ReviewProfile | str,
    *,
    stage_runtime_profile: StageRuntimeProfile | str = StageRuntimeProfile.TASK_LEVEL,
) -> EffectiveReviewPolicy:
    selection = review_profile_selection_from_value(value)
    profile = (
        default_review_profile_for_level(stage_runtime_profile)
        if selection == AUTO_REVIEW_PROFILE
        else ReviewProfile(selection)
    )
    revisits = _WORKFLOW_CONTROL_LIMITS_BY_PROFILE[profile]
    return EffectiveReviewPolicy(
        selected_profile=profile,
        max_total_revisits=revisits,
    )
