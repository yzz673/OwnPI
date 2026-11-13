"""B3 facts and deterministic projections; no process or snapshot reads."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Iterable

from ownpi.runtime.execution.board import _safe_text
from ownpi.runtime.execution.registry import ExecutionRegistry
from ownpi.runtime.execution.state import (
    PROCESS_OWNER_LOST_ISSUE, ExecutionHandle, ExecutionObservation,
    ExecutionOrigin, ExecutionRecord,
)
from ownpi.runtime.rollout.business_events import BusinessEvent


def execution_handle_data(handle: ExecutionHandle) -> dict[str, Any]:
    data = handle.to_dict()
    if handle.command_display:
        data['command_display'] = _safe_text(handle.command_display, 240)
    return data


def _locator_policy(data: dict[str, Any]) -> dict[str, Any]:
    """Do not transform usable locators; omit credential-bearing URLs explicitly."""
    result = dict(data)
    omitted: list[str] = []
    for key, value in data.items():
        if key != 'command_display' and isinstance(value, str) and re.search(
            r'[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s]*@', value,
        ):
            result[key] = ''
            omitted.append(key)
    if omitted:
        result['redacted_locator_fields'] = omitted
    return result


def _handle_fact(handle: ExecutionHandle) -> dict[str, Any]:
    return _locator_policy(execution_handle_data(handle))


def execution_state_evidence(observation: ExecutionObservation) -> dict[str, Any]:
    return {
        key: value for key in ('exit_code', 'closed', 'final_seq', 'timed_out',
                               'interrupted', 'job_state', 'failure_class')
        if isinstance(value := observation.evidence.get(key), (str, bool, int))
    }


def execution_transition_facts(
    previous: ExecutionRecord | None,
    record: ExecutionRecord,
    observation: ExecutionObservation,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Select facts at the domain commit boundary, not from legacy serialization."""
    common = {'execution_id': record.execution_id, 'observed_at': observation.observed_at}
    evidence = observation.evidence
    source = {key: evidence[key] for key in ('request_id', 'provider_call_id')
              if isinstance(evidence.get(key), str) and evidence[key]}
    source['tool_name'] = observation.origin.tool_name
    facts: list[tuple[str, dict[str, Any]]] = []
    if previous is None:
        facts.append(('execution_registered', {
            **common, 'started_at': record.started_at, 'launch_order': record.launch_order,
            'summary': _safe_text(record.summary, 240), 'origin': record.origin.to_dict(),
            'handle': _handle_fact(record.handle),
        }))
    elif execution_handle_data(previous.handle) != execution_handle_data(record.handle):
        facts.append(('execution_binding_updated', {
            **common, 'handle': _handle_fact(record.handle),
        }))
    if previous is None or (previous.status, previous.issue) != (record.status, record.issue):
        # A local owner failure is not proof that an external process exited.
        status = 'unknown' if record.issue == PROCESS_OWNER_LOST_ISSUE else record.status
        facts.append(('execution_state_confirmed', {
            **common, 'status': status, 'issue': _safe_text(record.issue, 2048),
            'source': source,
        }))
    if (record.readback_status or record.result_refs) and (
        previous is None or (previous.readback_status, previous.result_refs, previous.issue)
        != (record.readback_status, record.result_refs, record.issue)
    ):
        scope = 'local_terminal_observation'
        status = record.readback_status
        if record.issue == PROCESS_OWNER_LOST_ISSUE:
            scope, status = 'owner_lost_no_readback', 'unavailable'
        elif record.handle.backend == 'remote_workflow':
            scope = 'required_refs_present' if status == 'complete' else 'required_refs_pending'
        facts.append(('execution_readback_recorded', {
            **common, 'readback_status': status, 'result_refs': list(record.result_refs),
            'confirmation_scope': scope, 'content_verified': False,
            'issue': _safe_text(record.issue, 2048), 'source': source,
        }))
    return tuple(facts)


@dataclass(frozen=True)
class ExecutionEventProjection:
    registry: ExecutionRegistry


def project_execution_events(events: Iterable[BusinessEvent]) -> ExecutionEventProjection:
    """Rebuild all launches, including terminal/pending-readback, without I/O."""
    records: dict[str, ExecutionRecord] = {}
    seen: dict[str, BusinessEvent] = {}
    run_id: str | None = None
    revision = 0
    for event in events:
        if run_id is not None and event.run_id != run_id:
            raise ValueError('Execution projection cannot mix runs')
        run_id = event.run_id
        old = seen.get(event.event_id)
        if old is not None:
            if old.kind != event.kind or old.data != event.data:
                raise ValueError(f'Conflicting execution event: {event.event_id}')
            continue
        seen[event.event_id] = event
        if not event.kind.startswith('execution_'):
            continue
        data = event.data.to_object()
        execution_id = data.get('execution_id')
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError('Execution event requires execution_id')
        observed_at = data.get('observed_at')
        if not isinstance(observed_at, str):
            raise ValueError('Execution event requires observed_at')
        record = records.get(execution_id)
        if event.kind == 'execution_registered':
            if record is not None:
                raise ValueError(f'Duplicate execution registration: {execution_id}')
            record = ExecutionRecord(
                execution_id=execution_id, handle=_project_handle(data['handle']),
                origin=ExecutionOrigin.from_dict(data['origin']),
                started_at=data['started_at'], updated_at=observed_at, status='unknown',
                launch_order=int(data['launch_order']), summary=data['summary'],
            )
        elif record is None:
            raise ValueError(f'Execution fact precedes registration: {execution_id}')
        elif event.kind == 'execution_binding_updated':
            record = replace(record, handle=_project_handle(data['handle']))
        elif event.kind == 'execution_state_confirmed':
            if data['status'] not in {'running', 'unknown', 'blocked',
                                       'terminal_success', 'terminal_failure'}:
                raise ValueError('Invalid execution state')
            record = replace(record, status=data['status'], issue=data['issue'],
                             )
        elif event.kind == 'execution_readback_recorded':
            record = replace(record, readback_status=data['readback_status'],
                             result_refs=tuple(data['result_refs']), issue=data['issue'])
        else:
            raise ValueError(f'Unsupported execution event: {event.kind}')
        records[execution_id] = replace(record, updated_at=observed_at)
        revision += 1
    return ExecutionEventProjection(
        ExecutionRegistry(tuple(records.values()), revision),
    )


def _project_handle(data: dict[str, Any]) -> ExecutionHandle:
    return ExecutionHandle.from_dict({key: value for key, value in data.items()
                                      if key != 'redacted_locator_fields'})
