from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.events import now_utc_iso
from ownpi.tools.common import metadata_for_context

if TYPE_CHECKING:
    from ownpi.controller.stages import Stage
    from ownpi.runtime.state_models import RuntimeState
    from ownpi.runtime.tools.definitions import ToolExecutionContext


PLAN_UPDATED_MESSAGE = 'Plan updated'
VALID_PLAN_STATUSES = frozenset({'pending', 'in_progress', 'completed'})
LATEST_PLAN_SCRATCHPAD_KEY = 'update_plan_latest'
_MODEL_PAYLOAD_KEYS = frozenset({'explanation', 'plan'})
_MODEL_PLAN_ITEM_KEYS = frozenset({'step', 'status'})


async def run_update_plan(context: 'ToolExecutionContext', arguments: dict[str, Any]) -> ToolResult:
    state_store = getattr(context, 'state_store', None)
    if state_store is None:
        return ToolResult(ok=False, tool_name='update_plan', error='state store is not available')
    try:
        canonical = canonical_update_plan_payload(arguments)
    except ValueError as exc:
        return ToolResult(ok=False, tool_name='update_plan', error=f'failed to parse function arguments: {exc}')

    scratchpad = state_store.state.workflow_state.scratchpad
    update = {
        **canonical,
        'plan_revision': _next_plan_revision(scratchpad.get(LATEST_PLAN_SCRATCHPAD_KEY)),
        'stage': context.stage.value,
        'attempt': context.attempt,
        'continuation_index': context.continuation_index,
        'run_id': str(getattr(context, 'run_id', '') or ''),
        'actor_kind': str(getattr(context, 'actor_kind', '') or ''),
        'actor_id': str(getattr(context, 'actor_id', '') or ''),
        'work_assignment_identity': current_plan_work_assignment_identity(
            state_store.state,
            stage=context.stage,
            attempt=context.attempt,
        ),
        'updated_at': now_utc_iso(),
    }
    scratchpad[LATEST_PLAN_SCRATCHPAD_KEY] = update
    _touch(state_store)
    return ToolResult(
        ok=True,
        tool_name='update_plan',
        data={},
        stdout=PLAN_UPDATED_MESSAGE,
        metadata=metadata_for_context(
            context,
            plan_item_count=len(canonical['plan']),
            plan_revision=update['plan_revision'],
        ),
    )


def canonical_update_plan_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    explanation, plan = _parse_update_plan_arguments(arguments)
    return {
        'explanation': explanation,
        'plan': plan,
    }


def normalize_stored_update_plan_snapshot(value: Any) -> dict[str, Any] | None:
    """Normalize the current plan snapshot before incrementing its revision."""
    if not isinstance(value, dict):
        return None
    try:
        plan = _parse_stored_plan(value.get('plan'))
    except ValueError:
        return None
    raw_revision = value.get('plan_revision')
    revision = (
        raw_revision
        if isinstance(raw_revision, int) and not isinstance(raw_revision, bool) and raw_revision >= 1
        else 1
    )
    explanation = value.get('explanation')
    normalized: dict[str, Any] = {
        'explanation': str(explanation) if isinstance(explanation, str) else None,
        'plan_revision': revision,
        'plan': plan,
    }
    for key in (
        'stage',
        'attempt',
        'continuation_index',
        'run_id',
        'actor_kind',
        'actor_id',
        'work_assignment_identity',
        'updated_at',
        'owner_event_id',
    ):
        if key in value:
            normalized[key] = value.get(key)
    return normalized


def current_plan_work_assignment_identity(
    state: 'RuntimeState',
    *,
    stage: 'Stage',
    attempt: int,
) -> str:
    """Return the active feedback/revisit generation for one stage attempt."""
    stage_id = stage.value
    assignment_parts: list[tuple[str, str]] = []

    handoff = state.feedback_state.active_handoff
    if (
        handoff.status in {'pending', 'consumed'}
        and handoff.consumer_stage == stage_id
        and handoff.consumer_attempt == attempt
        and handoff.record_id
    ):
        assignment_parts.append(('feedback', handoff.record_id))

    revisit_context = state.workflow_state.revisit_state.current_context_for(
        stage=stage,
        attempt=attempt,
    )
    if revisit_context is not None:
        revisit_contract = revisit_context.get('revisit_contract')
        if isinstance(revisit_contract, dict):
            contract_id = str(revisit_contract.get('contract_id') or '').strip()
            if contract_id:
                assignment_parts.append(('revisit', contract_id))

    if not assignment_parts:
        return ''
    return json.dumps(assignment_parts, ensure_ascii=True, separators=(',', ':'))


def _parse_update_plan_arguments(
    arguments: dict[str, Any],
) -> tuple[str | None, list[dict[str, str]]]:
    if not isinstance(arguments, dict):
        raise ValueError('payload must be an object')
    unknown_keys = sorted(set(arguments) - _MODEL_PAYLOAD_KEYS)
    if unknown_keys:
        raise ValueError(f'unexpected fields: {", ".join(unknown_keys)}')
    explanation: str | None = None
    if 'explanation' in arguments and arguments.get('explanation') is not None:
        if not isinstance(arguments.get('explanation'), str):
            raise ValueError('explanation must be a string')
        explanation = str(arguments.get('explanation') or '')
    plan = _parse_model_plan(arguments.get('plan'))
    return explanation, plan


def _parse_model_plan(raw_plan: Any) -> list[dict[str, str]]:
    if not isinstance(raw_plan, list):
        raise ValueError('plan must be an array')
    plan: list[dict[str, str]] = []
    in_progress_count = 0
    for index, raw_item in enumerate(raw_plan):
        if not isinstance(raw_item, dict):
            raise ValueError(f'plan[{index}] must be an object')
        unknown_keys = sorted(set(raw_item) - _MODEL_PLAN_ITEM_KEYS)
        if unknown_keys:
            raise ValueError(f'plan[{index}] has unexpected fields: {", ".join(unknown_keys)}')
        raw_step = raw_item.get('step')
        if not isinstance(raw_step, str) or not raw_step.strip():
            raise ValueError(f'plan[{index}].step must be a non-empty string')
        raw_status = raw_item.get('status')
        if not isinstance(raw_status, str):
            raise ValueError(f'plan[{index}].status must be a string')
        status = raw_status.strip()
        if status not in VALID_PLAN_STATUSES:
            allowed = ', '.join(sorted(VALID_PLAN_STATUSES))
            raise ValueError(f'plan[{index}].status must be one of: {allowed}')
        if status == 'in_progress':
            in_progress_count += 1
        plan.append({'step': raw_step.strip(), 'status': status})
    if in_progress_count > 1:
        raise ValueError('at most one plan item can be in_progress')
    return plan


def _parse_stored_plan(raw_plan: Any) -> list[dict[str, str]]:
    if not isinstance(raw_plan, list):
        raise ValueError('stored plan must be an array')
    stripped = [
        {
            'step': item.get('step'),
            'status': item.get('status'),
        }
        if isinstance(item, dict)
        else item
        for item in raw_plan
    ]
    return _parse_model_plan(stripped)


def _next_plan_revision(previous: Any) -> int:
    normalized = normalize_stored_update_plan_snapshot(previous)
    if normalized is None:
        return 1
    return int(normalized['plan_revision']) + 1


def _touch(state_store: Any) -> None:
    touch = getattr(state_store, '_touch', None)
    if callable(touch):
        touch()
