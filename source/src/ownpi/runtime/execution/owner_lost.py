from __future__ import annotations

from ownpi.runtime.events import now_utc_iso
from ownpi.runtime.execution.state import (
    PROCESS_OWNER_LOST_ISSUE,
    ExecutionObservation,
    ExecutionOrigin,
    ExecutionRecord,
)


def owner_lost_observations(
    records: tuple[ExecutionRecord, ...],
    *,
    owner_id: str,
    process_id: str = "",
) -> tuple[ExecutionObservation, ...]:
    return tuple(
        ExecutionObservation(
            execution_id=record.execution_id,
            status="unknown",
            handle=record.handle,
            origin=ExecutionOrigin(
                stage=record.origin.stage,
                attempt=record.origin.attempt,
                continuation_index=record.origin.continuation_index,
                tool_name="process_owner_lifecycle",
            ),
            observed_at=now_utc_iso(),
            result_refs=record.result_refs,
            readback_status="unavailable",
            issue=PROCESS_OWNER_LOST_ISSUE,
        )
        for record in records
        if record.is_active
        and record.handle.owner_id == owner_id
        and (not process_id or record.handle.process_id == process_id)
    )


__all__ = ["owner_lost_observations"]
