from __future__ import annotations

from ownpi.runtime.stage_prep.models import (
    StagePrepHandoff,
    StagePrepRecord,
)
from ownpi.static_semantic_contracts import resolve_stage_contract
from ownpi.schemas.workflow_plan import StageRuntimeProfile
from ownpi.controller.stages import Stage


def build_stage_prep_handoff(
    *,
    record: StagePrepRecord,
    loop_index: int = 1,
) -> StagePrepHandoff:
    if record.status != "completed":
        raise ValueError("stage prep handoff requires a completed record")
    bundle = resolve_stage_contract(Stage(record.stage), StageRuntimeProfile(record.level))
    if loop_index > len(bundle.loop_contracts):
        raise ValueError('stage loop index is out of range')
    return StagePrepHandoff(
        prep_id=record.prep_id,
        loop_count=len(bundle.loop_contracts),
    )


__all__ = [
    "build_stage_prep_handoff",
]
