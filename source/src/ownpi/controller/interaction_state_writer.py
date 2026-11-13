from __future__ import annotations

from typing import Any

from ownpi.runtime.events import now_utc_iso
from ownpi.schemas.runtime_human_clarification import HumanClarificationSession
from ownpi.schemas.runtime_permission import ToolPermissionSession
from ownpi.schemas.runtime_tooling import ToolObservation


def _normalized_tool_permission_decision(decision: Any) -> str:
    normalized = str(decision or '').strip().lower().replace('-', '_')
    if normalized in {'deny_current_command', 'reject_command', 'reject_current_command'}:
        return 'deny_command'
    return normalized


def record_denied_tool_command_resume_guidance(
    store: Any,
    *,
    session: ToolPermissionSession,
    created_at: str | None = None,
) -> ToolObservation | None:
    if _normalized_tool_permission_decision(session.decision) != 'deny_command':
        return None
    observation_id = f'tool_permission_guidance:{session.session_id}'
    existing = next(
        (
            item
            for item in store.state.tool_state.recent_observations
            if item.observation_id == observation_id
        ),
        None,
    )
    if existing is not None:
        return existing
    timestamp = created_at or now_utc_iso()
    summary = (
        'The human rejected this exact command/input. Do not retry same tool input; '
        'choose a narrower command, safer alternative, or ask clarification. '
        f'decision=deny_command session_id={session.session_id} '
        f'request_id={session.request_id} input_digest={session.input_digest or "<missing>"}'
    )
    observation = ToolObservation(
        observation_id=observation_id,
        source_type='permission_guidance',
        source_name=session.tool_name,
        stage=session.stage,
        attempt=session.attempt,
        query_chain_id=session.query_chain_id,
        query_chain_depth=session.query_chain_depth,
        continuation_index=session.continuation_index,
        summary=summary,
        payload_ref=f'tool_permission:{session.session_id}',
        created_at=timestamp,
    )
    store.record_tool_observation(observation)
    return observation


def archive_answered_human_clarification(
    store: Any,
    *,
    session_id: str,
) -> HumanClarificationSession | None:
    session = store.state.workflow_state.pending_human_clarification
    if session is None or session.session_id != session_id:
        return None
    if session.status != 'answered':
        raise RuntimeError(f'Human clarification session is not answered: {session.status}')
    store.state.workflow_state.pending_human_clarification = None
    store.state.workflow_state.human_clarification_history.append(session)
    store._touch()
    return session
