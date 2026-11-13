from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ownpi.stage_ids import canonical_stage_id


def _read_continuation_index(data: dict[str, Any]) -> int:
    if data.get('continuation_index') is not None:
        return int(data.get('continuation_index', 0) or 0)
    return 0


def _read_query_chain_id(data: dict[str, Any]) -> str:
    if data.get('query_chain_id') is not None:
        return str(data.get('query_chain_id', '') or '').strip()
    return ''


def _read_query_chain_depth(data: dict[str, Any]) -> int:
    if data.get('query_chain_depth') is not None:
        return int(data.get('query_chain_depth', 0) or 0)
    return 0


@dataclass(frozen=True)
class ToolPermissionSession:
    session_id: str
    request_id: str
    tool_name: str
    permission_class: str
    stage: str
    attempt: int
    query_chain_id: str = ''
    query_chain_depth: int = 0
    continuation_index: int = 0
    actor_kind: str = 'core'
    actor_id: str | None = None
    conversation_lane: str = ''
    conversation_exchange_id: str = ''
    output_sequence_index: int | None = None
    critic_kind: str = 'none'
    critic_round: int = 0
    critic_phase: str = 'idle'
    base_permission_class: str | None = None
    destructive_action: bool = False
    destructive_reason: str = ''
    destructive_kinds: tuple[str, ...] = ()
    requested_by: str = 'agent'
    continuation_kind: str = 'continuation_runtime'
    status: str = 'pending'
    decision: str | None = None
    decision_scope: str | None = None
    human_prompt: str = ''
    reason: str = ''
    input_digest: str = ''
    approval_fingerprint: str = ''
    base_policy_hash: str = ''
    reviewer: str = 'user'
    approval_request: dict[str, Any] = field(default_factory=dict)
    approved_overlay: dict[str, Any] | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    answered_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolPermissionSession':
        return cls(
            session_id=str(data.get('session_id', '')).strip(),
            request_id=str(data.get('request_id', '')).strip(),
            tool_name=str(data.get('tool_name', '')).strip(),
            permission_class=str(data.get('permission_class', 'default') or 'default'),
            base_permission_class=(
                str(data['base_permission_class']) if data.get('base_permission_class') is not None else None
            ),
            destructive_action=bool(data.get('destructive_action', False)),
            destructive_reason=str(data.get('destructive_reason', '') or ''),
            destructive_kinds=tuple(
                str(item) for item in data.get('destructive_kinds', []) if str(item).strip()
            ),
            stage=canonical_stage_id(data.get('stage', '')).strip(),
            attempt=int(data.get('attempt', 0) or 0),
            query_chain_id=_read_query_chain_id(data),
            query_chain_depth=_read_query_chain_depth(data),
            continuation_index=_read_continuation_index(data),
            actor_kind=str(data.get('actor_kind', 'core') or 'core'),
            actor_id=(str(data['actor_id']) if data.get('actor_id') is not None else None),
            conversation_lane=str(data.get('conversation_lane', '') or ''),
            conversation_exchange_id=str(data.get('conversation_exchange_id', '') or ''),
            output_sequence_index=data.get('output_sequence_index'),
            critic_kind=str(data.get('critic_kind', 'none') or 'none'),
            critic_round=int(data.get('critic_round', 0) or 0),
            critic_phase=str(data.get('critic_phase', 'idle') or 'idle'),
            requested_by=str(data.get('requested_by', 'agent') or 'agent'),
            continuation_kind=str(data.get('continuation_kind', 'continuation_runtime') or 'continuation_runtime'),
            status=str(data.get('status', 'pending') or 'pending'),
            decision=(str(data['decision']) if data.get('decision') is not None else None),
            decision_scope=(
                str(data['decision_scope'])
                if data.get('decision_scope') is not None
                else (str(data['scope']) if data.get('scope') is not None else None)
            ),
            human_prompt=str(data.get('human_prompt', '') or ''),
            reason=str(data.get('reason', '') or ''),
            input_digest=str(data.get('input_digest', '') or ''),
            approval_fingerprint=str(data.get('approval_fingerprint', '') or ''),
            base_policy_hash=str(data.get('base_policy_hash', '') or ''),
            reviewer=str(data.get('reviewer', 'user') or 'user'),
            approval_request=(
                dict(data.get('approval_request', {}))
                if isinstance(data.get('approval_request'), dict)
                else {}
            ),
            approved_overlay=(
                dict(data['approved_overlay'])
                if isinstance(data.get('approved_overlay'), dict)
                else None
            ),
            request_payload=dict(data.get('request_payload', {}))
            if isinstance(data.get('request_payload'), dict)
            else {},
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
            answered_at=(str(data['answered_at']) if data.get('answered_at') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload['request_payload'] = dict(self.request_payload)
        payload['approval_request'] = dict(self.approval_request)
        payload['approved_overlay'] = (
            dict(self.approved_overlay) if self.approved_overlay is not None else None
        )
        return payload


@dataclass(frozen=True)
class SessionPermissionGrant:
    grant_id: str
    run_id: str
    runtime_session_id: str
    task_id: str
    tool_name: str
    action: str
    environment_hash: str
    base_policy_hash: str
    reviewer: str
    approved_overlay: dict[str, Any]
    exact_input_hash: str | None = None
    command_prefix: tuple[str, ...] = ()
    created_at: str | None = None
    session_match_kind: str = ''
    session_match_version: int | None = None
    session_match_digest: str = ''

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SessionPermissionGrant':
        from ownpi.runtime.permissions.session_identity import read_session_identity

        overlay = data.get('approved_overlay')
        if not isinstance(overlay, dict):
            raise ValueError('session permission grant is missing approved_overlay')
        grant = cls(
            grant_id=str(data.get('grant_id', '') or '').strip(),
            run_id=str(data.get('run_id', '') or '').strip(),
            runtime_session_id=str(data.get('runtime_session_id', '') or '').strip(),
            task_id=str(data.get('task_id', '') or '').strip(),
            tool_name=str(data.get('tool_name', '') or '').strip(),
            action=str(data.get('action', '') or '').strip(),
            environment_hash=str(data.get('environment_hash', '') or '').strip(),
            base_policy_hash=str(data.get('base_policy_hash', '') or '').strip(),
            reviewer=str(data.get('reviewer', '') or '').strip(),
            approved_overlay=dict(overlay),
            exact_input_hash=(
                str(data['exact_input_hash'])
                if data.get('exact_input_hash') is not None
                else None
            ),
            command_prefix=tuple(
                str(item) for item in data.get('command_prefix', []) if str(item).strip()
            ),
            created_at=(str(data['created_at']) if data.get('created_at') is not None else None),
            **read_session_identity(data),
        )
        required_values = {
            'grant_id': grant.grant_id,
            'run_id': grant.run_id,
            'runtime_session_id': grant.runtime_session_id,
            'task_id': grant.task_id,
            'tool_name': grant.tool_name,
            'action': grant.action,
            'environment_hash': grant.environment_hash,
            'base_policy_hash': grant.base_policy_hash,
            'reviewer': grant.reviewer,
        }
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            raise ValueError(
                'session permission grant is missing required bindings: '
                + ', '.join(missing)
            )
        from ownpi.runtime.permissions.models import (
            ApprovalsReviewer,
            PermissionRequest,
        )

        ApprovalsReviewer(grant.reviewer)
        PermissionRequest.from_dict(
            {
                'request_id': grant.grant_id,
                'tool_name': grant.tool_name,
                'action': grant.action,
                'normalized_input_hash': grant.exact_input_hash or 'bounded-path-input',
                'environment_hash': grant.environment_hash,
                'base_policy_hash': grant.base_policy_hash,
                'requested_overlay': grant.approved_overlay,
            }
        )
        return grant

    def to_dict(self) -> dict[str, Any]:
        return {
            'grant_id': self.grant_id,
            'run_id': self.run_id,
            'runtime_session_id': self.runtime_session_id,
            'task_id': self.task_id,
            'tool_name': self.tool_name,
            'action': self.action,
            'environment_hash': self.environment_hash,
            'base_policy_hash': self.base_policy_hash,
            'reviewer': self.reviewer,
            'approved_overlay': dict(self.approved_overlay),
            'exact_input_hash': self.exact_input_hash,
            'command_prefix': list(self.command_prefix),
            'session_match_kind': self.session_match_kind,
            'session_match_version': self.session_match_version,
            'session_match_digest': self.session_match_digest,
            'created_at': self.created_at,
        }
