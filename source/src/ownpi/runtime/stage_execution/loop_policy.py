"""Execution policy for the current staged substage."""

from ownpi.controller.stages import Stage
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.static_semantic_contracts import resolve_stage_contract


def is_analysis_route_loop(
    stage: Stage, profile: StageRuntimeProfile, loop_index: int,
) -> bool:
    if stage != Stage.ANALYSIS_DECISION:
        return False
    count = len(resolve_stage_contract(stage, profile).loop_contracts)
    if not 1 <= loop_index <= count:
        raise ValueError('stage loop index is out of range')
    return loop_index == count
