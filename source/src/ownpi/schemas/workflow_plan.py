"""Mechanical workflow and runtime-profile schemas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ownpi.controller.stages import ACTIVE_STAGE_ORDER, Stage, stage_from_value


class ExecutionTier(StrEnum):
    DIRECT = 'direct'
    FULL = 'full'


class StageRuntimeProfile(StrEnum):
    TASK_LEVEL = 'task_level'
    RESEARCH_LEVEL = 'research_level'


def execution_tier_for_profile(profile: StageRuntimeProfile) -> ExecutionTier:
    return (ExecutionTier.DIRECT if profile is StageRuntimeProfile.TASK_LEVEL
            else ExecutionTier.FULL)


class RuntimeProfileSelection(StrEnum):
    AUTO = 'auto'
    TASK = 'task'
    RESEARCH = 'research'


@dataclass(frozen=True)
class RuntimeProfileResolution:
    requested: RuntimeProfileSelection
    resolved: StageRuntimeProfile
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            'runtime_profile_requested': self.requested.value,
            'stage_runtime_profile': self.resolved.value,
            'runtime_profile_source': self.source,
        }


def runtime_profile_selection_from_value(
    value: str | RuntimeProfileSelection | StageRuntimeProfile | None,
) -> RuntimeProfileSelection:
    if value is None:
        return RuntimeProfileSelection.AUTO
    if isinstance(value, RuntimeProfileSelection):
        return value
    if isinstance(value, StageRuntimeProfile):
        return (
            RuntimeProfileSelection.RESEARCH
            if value == StageRuntimeProfile.RESEARCH_LEVEL
            else RuntimeProfileSelection.TASK
        )
    normalized = str(value or '').strip().casefold().replace('-', '_')
    aliases = {
        '': RuntimeProfileSelection.AUTO,
        RuntimeProfileSelection.AUTO.value: RuntimeProfileSelection.AUTO,
        RuntimeProfileSelection.TASK.value: RuntimeProfileSelection.TASK,
        RuntimeProfileSelection.RESEARCH.value: RuntimeProfileSelection.RESEARCH,
        StageRuntimeProfile.TASK_LEVEL.value: RuntimeProfileSelection.TASK,
        StageRuntimeProfile.RESEARCH_LEVEL.value: RuntimeProfileSelection.RESEARCH,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            f'Unsupported runtime profile: {value!r}. Expected auto, task, or research.'
        ) from exc


def stage_runtime_profile_from_selection(
    selection: str | RuntimeProfileSelection | StageRuntimeProfile | None,
) -> StageRuntimeProfile | None:
    normalized = runtime_profile_selection_from_value(selection)
    if normalized == RuntimeProfileSelection.AUTO:
        return None
    if normalized == RuntimeProfileSelection.RESEARCH:
        return StageRuntimeProfile.RESEARCH_LEVEL
    return StageRuntimeProfile.TASK_LEVEL


def runtime_profile_selection_for_stage_profile(
    profile: StageRuntimeProfile,
) -> RuntimeProfileSelection:
    return runtime_profile_selection_from_value(profile)


def resolve_runtime_profile(
    *,
    invocation_selection: str | RuntimeProfileSelection | StageRuntimeProfile | None = None,
    session_selection: str | RuntimeProfileSelection | StageRuntimeProfile | None = None,
    task_profile: StageRuntimeProfile | None = None,
    task_requested: str | RuntimeProfileSelection | StageRuntimeProfile | None = None,
    task_source: str = 'task_spec',
    config_selection: str | RuntimeProfileSelection | StageRuntimeProfile | None = None,
    invocation_source: str = 'invocation',
) -> RuntimeProfileResolution:
    invocation = (
        runtime_profile_selection_from_value(invocation_selection)
        if invocation_selection is not None
        else None
    )
    session = (
        runtime_profile_selection_from_value(session_selection)
        if session_selection is not None
        else None
    )
    config = runtime_profile_selection_from_value(config_selection)
    if invocation is not None:
        requested = invocation
    elif session is not None:
        requested = session
    elif task_profile is not None:
        try:
            requested = (
                runtime_profile_selection_from_value(task_requested)
                if task_requested is not None
                else runtime_profile_selection_for_stage_profile(task_profile)
            )
        except ValueError:
            requested = runtime_profile_selection_for_stage_profile(task_profile)
    elif task_requested is not None:
        requested = runtime_profile_selection_from_value(task_requested)
    elif config_selection is not None:
        requested = config
    else:
        requested = RuntimeProfileSelection.AUTO

    if invocation is not None and invocation != RuntimeProfileSelection.AUTO:
        return RuntimeProfileResolution(
            requested=requested,
            resolved=stage_runtime_profile_from_selection(invocation)
            or StageRuntimeProfile.TASK_LEVEL,
            source=str(invocation_source or 'invocation'),
        )
    if session is not None and session != RuntimeProfileSelection.AUTO:
        return RuntimeProfileResolution(
            requested=requested,
            resolved=stage_runtime_profile_from_selection(session)
            or StageRuntimeProfile.TASK_LEVEL,
            source='chat_session',
        )
    if task_profile is not None:
        return RuntimeProfileResolution(
            requested=requested,
            resolved=task_profile,
            source=str(task_source or 'task_spec'),
        )
    if config != RuntimeProfileSelection.AUTO:
        return RuntimeProfileResolution(
            requested=requested,
            resolved=stage_runtime_profile_from_selection(config)
            or StageRuntimeProfile.TASK_LEVEL,
            source='config',
        )
    return RuntimeProfileResolution(
        requested=requested,
        resolved=StageRuntimeProfile.TASK_LEVEL,
        source='default',
    )


@dataclass(frozen=True)
class RunWorkflowPlan:
    stage_sequence: tuple[Stage, ...]

    def __post_init__(self) -> None:
        try:
            stages = tuple(stage_from_value(stage) for stage in self.stage_sequence)
        except (TypeError, ValueError) as exc:
            raise ValueError('RunWorkflowPlan contains an unknown stage.') from exc
        if not stages:
            raise ValueError('RunWorkflowPlan stage_sequence must not be empty.')
        if len(stages) != len(set(stages)):
            raise ValueError('RunWorkflowPlan stage_sequence must contain unique stages.')

        forbidden = {Stage.DIRECT_QA}
        invalid = tuple(stage for stage in stages if stage in forbidden)
        if invalid:
            names = ', '.join(stage.value for stage in invalid)
            raise ValueError(f'RunWorkflowPlan contains forbidden full-tier stages: {names}.')

        canonical_order = (*ACTIVE_STAGE_ORDER, Stage.OUTPUT_SYNTHESIS)
        canonical_index = {stage: index for index, stage in enumerate(canonical_order)}
        if any(stage not in canonical_index for stage in stages):
            raise ValueError('RunWorkflowPlan contains a non-canonical stage.')
        if tuple(sorted(stages, key=canonical_index.__getitem__)) != stages:
            raise ValueError('RunWorkflowPlan stage_sequence must follow canonical stage order.')
        if stages[-1] != Stage.OUTPUT_SYNTHESIS:
            raise ValueError('RunWorkflowPlan must terminate with output_synthesis.')

        object.__setattr__(self, 'stage_sequence', stages)

    @classmethod
    def from_selected_stages(cls, stages: tuple[Stage | str, ...]) -> 'RunWorkflowPlan':
        try:
            normalized = tuple(stage_from_value(stage) for stage in stages)
        except (TypeError, ValueError) as exc:
            raise ValueError('RunWorkflowPlan contains an unknown stage.') from exc
        return cls(stage_sequence=normalized)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'RunWorkflowPlan':
        if set(data) != {'stage_sequence'}:
            raise ValueError('RunWorkflowPlan payload must contain only stage_sequence.')
        raw_sequence = data['stage_sequence']
        if not isinstance(raw_sequence, (list, tuple)):
            raise ValueError('RunWorkflowPlan stage_sequence must be a list or tuple.')
        return cls.from_selected_stages(tuple(raw_sequence))

    def to_dict(self) -> dict[str, Any]:
        return {'stage_sequence': [stage.value for stage in self.stage_sequence]}

    @property
    def entry_stage(self) -> Stage:
        return self.stage_sequence[0]

    @property
    def terminal_stage(self) -> Stage:
        return self.stage_sequence[-1]

    def contains(self, stage: Stage | str) -> bool:
        return stage_from_value(stage) in self.stage_sequence

    def index(self, stage: Stage | str) -> int:
        normalized = stage_from_value(stage)
        try:
            return self.stage_sequence.index(normalized)
        except ValueError as exc:
            raise ValueError(
                f'Stage {normalized.value} is not present in this workflow plan.'
            ) from exc

    def next_stage(self, stage: Stage | str) -> Stage | None:
        index = self.index(stage)
        if index + 1 >= len(self.stage_sequence):
            return None
        return self.stage_sequence[index + 1]

    def prior_stages(self, stage: Stage | str) -> tuple[Stage, ...]:
        return self.stage_sequence[: self.index(stage)]

    def is_prior_stage(self, current: Stage | str, target: Stage | str) -> bool:
        normalized_target = stage_from_value(target)
        return normalized_target in self.prior_stages(current)

    def validate_revisit_target(self, current: Stage | str, target: Stage | str) -> Stage:
        normalized_target = stage_from_value(target)
        if not self.is_prior_stage(current, normalized_target):
            raise ValueError(
                f'Stage {normalized_target.value} is not a prior stage of '
                f'{stage_from_value(current).value} in this workflow plan.'
            )
        return normalized_target
