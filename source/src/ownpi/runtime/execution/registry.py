from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ownpi.runtime.execution.state import (
    ExecutionObservation,
    ExecutionRecord,
    handles_refer_to_same_execution,
    merge_execution_record,
    record_from_observation,
)


@dataclass(frozen=True)
class RegistryCommit:
    registry: 'ExecutionRegistry'
    record: ExecutionRecord
    material_change: bool
    created: bool


@dataclass(frozen=True)
class ExecutionRegistry:
    records: tuple[ExecutionRecord, ...] = ()
    material_revision: int = 0

    @classmethod
    def from_dict(cls, payload: Any) -> 'ExecutionRegistry':
        if not isinstance(payload, dict):
            return cls()
        items = payload.get('records')
        if not isinstance(items, list):
            return cls()
        return cls(
            records=tuple(
                ExecutionRecord.from_dict(item)
                for item in items
                if isinstance(item, dict)
            ),
            material_revision=max(0, int(payload.get('material_revision', 0) or 0)),
        )

    @property
    def active_non_terminal(self) -> tuple[ExecutionRecord, ...]:
        return tuple(record for record in self.records if record.is_active)

    def get(self, execution_id: str) -> ExecutionRecord | None:
        return next(
            (record for record in self.records if record.execution_id == execution_id),
            None,
        )

    def execution_ids_for_owner_process(
        self,
        *,
        owner_id: str,
        process_id: str,
    ) -> tuple[str, ...]:
        return tuple(
            record.execution_id
            for record in self.records
            if record.handle.owner_id == owner_id
            and record.handle.process_id == process_id
        )

    def execution_ids_for_owner(self, *, owner_id: str) -> tuple[str, ...]:
        return tuple(
            record.execution_id
            for record in self.records
            if record.handle.owner_id == owner_id
        )

    def commit(
        self, observation: ExecutionObservation, *, launch_order_floor: int = 0,
    ) -> RegistryCommit:
        match = self.matching_record(observation)
        if match is None:
            record = record_from_observation(
                observation,
                launch_order=max(launch_order_floor, max(
                    (item.launch_order for item in self.records), default=0,
                )) + 1,
            )
            registry = ExecutionRegistry(
                records=(*self.records, record),
                material_revision=self.material_revision + 1,
            )
            return RegistryCommit(registry, record, True, True)
        normalized = observation
        if observation.execution_id != match.execution_id:
            normalized = ExecutionObservation(
                execution_id=match.execution_id,
                status=observation.status,
                handle=observation.handle,
                origin=observation.origin,
                observed_at=observation.observed_at,
                result_refs=observation.result_refs,
                readback_status=observation.readback_status,
                issue=observation.issue,
                evidence=observation.evidence,
            )
        merged, material = merge_execution_record(match, normalized)
        if not material:
            return RegistryCommit(self, match, False, False)
        records = tuple(
            merged if item.execution_id == match.execution_id else item
            for item in self.records
        )
        registry = ExecutionRegistry(
            records=records,
            material_revision=self.material_revision + 1,
        )
        return RegistryCommit(registry, merged, True, False)

    def to_dict(self) -> dict[str, Any]:
        return {
            'material_revision': self.material_revision,
            'records': [record.to_dict() for record in self.records],
        }

    def matching_record(self, observation: ExecutionObservation) -> ExecutionRecord | None:
        direct = self.get(observation.execution_id)
        if direct is not None:
            return direct
        return next(
            (
                record
                for record in self.records
                if record.is_active
                and handles_refer_to_same_execution(record.handle, observation.handle)
            ),
            None,
        )
