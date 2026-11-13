from __future__ import annotations

from dataclasses import replace
from pathlib import PurePath

from ownpi.runtime.execution.state import (
    PROCESS_OWNER_LOST_ISSUE,
    ExecutionObservation,
    ExecutionRecord,
)


_CONTROL_ARTIFACTS = frozenset(
    {'status.json', 'manifest.json', 'job_handle.json', 'exit_status.json'}
)


def assess_terminal_readback(
    observation: ExecutionObservation,
) -> ExecutionObservation:
    if not observation.terminal:
        return observation
    if observation.issue == PROCESS_OWNER_LOST_ISSUE:
        return replace(observation, readback_status='complete')
    if observation.handle.backend != 'remote_workflow':
        return replace(observation, readback_status='complete')
    if observation.origin.tool_name != 'remote_pull':
        return replace(
            observation,
            readback_status='pending',
            issue='Terminal remote execution requires local artifact readback.',
        )
    missing = _missing_required_refs(observation.result_refs)
    if missing:
        return replace(
            observation,
            readback_status='pending',
            issue=f'Terminal readback is missing: {", ".join(missing)}.',
        )
    return replace(observation, readback_status='complete', issue='')


def terminal_readback_complete(record: ExecutionRecord) -> bool:
    return not record.terminal or record.readback_status == 'complete'


def _missing_required_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    names = {
        PurePath(str(ref).strip().rstrip('/')).name
        for ref in refs
        if str(ref).strip()
    }
    missing = [name for name in ('status.json', 'manifest.json') if name not in names]
    if not any(name and name not in _CONTROL_ARTIFACTS for name in names):
        missing.append('non-control result artifact')
    return tuple(missing)


__all__ = ['assess_terminal_readback', 'terminal_readback_complete']
