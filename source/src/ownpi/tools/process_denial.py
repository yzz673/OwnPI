from __future__ import annotations

from typing import Any, Mapping

from ownpi.runtime.permissions.models import EffectivePermissions
from ownpi.runtime.permissions.orchestrator import sandbox_denial_next_action
from ownpi.runtime.sandbox.denial import is_sandbox_denied


def process_denial_projection(
    snapshot: Mapping[str, Any],
    *,
    sandbox_backend: str,
    effective_permissions: EffectivePermissions | dict[str, Any] | None,
    interrupted: bool = False,
) -> dict[str, Any]:
    raw_evidence = snapshot.get('denial_evidence')
    evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
    actual_backend = str(snapshot.get('sandbox_backend') or sandbox_backend)
    denied = is_sandbox_denied(
        sandbox_backend=actual_backend,
        closed=bool(snapshot.get('closed')),
        exit_code=snapshot.get('exit_code'),
        matched_marker=str(evidence.get('matched_marker') or ''),
        executor_reported_denial=snapshot.get('sandbox_denied') is True,
        timed_out=bool(snapshot.get('timed_out')),
        interrupted=interrupted or bool(snapshot.get('interrupted')),
    )
    if not denied:
        return {}
    permissions = effective_permissions
    if isinstance(permissions, dict):
        permissions = EffectivePermissions.from_dict(permissions)
    return {
        'failure_class': 'sandbox_denied',
        'failure_kind': 'sandbox_denied',
        'sandbox_backend': actual_backend,
        'denial_evidence': dict(evidence),
        'recommended_next_action': sandbox_denial_next_action(permissions),
    }
