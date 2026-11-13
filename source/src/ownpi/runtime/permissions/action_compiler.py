from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ownpi.runtime.permissions.input_identity import permission_input_hash
from ownpi.runtime.permissions.orchestrator import (
    FileSystemAccessRequest,
    PermissionOperation,
)


@dataclass(frozen=True)
class CanonicalAction:
    tool_name: str
    permission_class: str
    normalized_input: Mapping[str, Any]
    effects: 'OperationEffects'
    operation: PermissionOperation
    action_hash: str


@dataclass(frozen=True)
class OperationEffects:
    file_system_accesses: tuple[FileSystemAccessRequest, ...] = ()
    network_required: bool = False
    network_endpoints: tuple[str, ...] = ()
    process_required: bool = False
    process_argv: tuple[str, ...] = ()
    remote_required: bool = False
    remote_target: str = ''
    remote_workdir: str = ''
    sources: tuple[str, ...] = ()
    effect_hash: str = ''
    session_identity: str = ''

    @classmethod
    def declared(
        cls,
        *,
        file_system_accesses: tuple[FileSystemAccessRequest, ...] = (),
        network_required: bool = False,
        network_endpoints: tuple[str, ...] = (),
        process_required: bool = False,
        process_argv: tuple[str, ...] = (),
        remote_required: bool = False,
        remote_target: str = '',
        remote_workdir: str = '',
        session_identity: str = '',
        source: str,
    ) -> 'OperationEffects':
        return cls(
            file_system_accesses=file_system_accesses,
            network_required=network_required,
            network_endpoints=network_endpoints,
            process_required=process_required,
            process_argv=process_argv,
            remote_required=remote_required,
            remote_target=remote_target,
            remote_workdir=remote_workdir,
            sources=(source,),
            session_identity=session_identity,
        )


def compile_canonical_action(
    *,
    request_id: str,
    tool_name: str,
    permission_class: str,
    input_payload: Mapping[str, Any],
    environment: Mapping[str, Any],
    destructive: bool,
    destructive_reason: str = '',
    declared_effects: OperationEffects | None = None,
) -> CanonicalAction:
    normalized_input = _normalize_mapping(input_payload)
    effects = _declared_effects(declared_effects)
    escalation_requested = (
        str(normalized_input.get('sandbox_permissions', 'use_default') or 'use_default')
        == 'require_escalated'
    )
    operation_environment = dict(environment)
    approval_reason = destructive_reason if destructive else ''
    if escalation_requested:
        approval_reason = str(normalized_input.get('justification', '') or '').strip()
    operation = PermissionOperation(
        request_id=request_id,
        tool_name=tool_name,
        action=f'{permission_class}:{tool_name}',
        input_payload=normalized_input,
        environment=operation_environment,
        file_system_accesses=effects.file_system_accesses,
        network_required=effects.network_required,
        process_required=effects.process_required,
        process_argv=effects.process_argv,
        remote_required=effects.remote_required,
        network_endpoints=effects.network_endpoints,
        remote_target=effects.remote_target,
        remote_workdir=effects.remote_workdir,
        effect_hash=effects.effect_hash,
        session_identity=effects.session_identity,
        approval_required_reason=approval_reason,
    )
    action_hash = permission_input_hash(
        {
                'tool_name': tool_name,
                'permission_class': permission_class,
                'input': normalized_input,
                'environment': operation_environment,
                'effects': effects.effect_hash,
                'destructive': destructive,
        }
    )
    return CanonicalAction(
        tool_name=tool_name,
        permission_class=permission_class,
        normalized_input=normalized_input,
        effects=effects,
        operation=operation,
        action_hash=action_hash,
    )


def _declared_effects(declared_effects: OperationEffects | None) -> OperationEffects:
    """Return only effects explicitly declared by the handler.

    Permission classification must not guess from tool names, metadata, or shell
    text. Unknown shell effects are enforced by the execution sandbox.
    """
    declared = declared_effects or OperationEffects()
    accesses = tuple(dict.fromkeys(declared.file_system_accesses))
    effect_payload = {
        'file_system_accesses': [
            {'path': str(item.path), 'access': item.access.value}
            for item in accesses
        ],
        'network_required': declared.network_required,
        'network_endpoints': list(declared.network_endpoints),
        'process_required': declared.process_required,
        'process_argv': list(declared.process_argv),
        'remote_required': declared.remote_required,
        'remote_target': declared.remote_target,
        'remote_workdir': declared.remote_workdir,
        'sources': list(declared.sources),
        'session_identity': declared.session_identity,
    }
    effect_hash = hashlib.sha256(
        json.dumps(
            effect_payload,
            sort_keys=True,
            separators=(',', ':'),
            default=str,
        ).encode('utf-8')
    ).hexdigest()
    return OperationEffects(
        file_system_accesses=accesses,
        network_required=declared.network_required,
        network_endpoints=declared.network_endpoints,
        process_required=declared.process_required,
        process_argv=declared.process_argv,
        remote_required=declared.remote_required,
        remote_target=declared.remote_target,
        remote_workdir=declared.remote_workdir,
        sources=declared.sources,
        effect_hash=effect_hash,
        session_identity=declared.session_identity,
    )


def _normalize_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _normalize_value(value)
        for key, value in sorted(payload.items(), key=lambda item: str(item[0]))
    }


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return _normalize_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value
