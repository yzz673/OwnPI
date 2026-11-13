"""Rebuild readable feedback from committed facts, never from worker status."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from ownpi.subagent.contracts import SubagentResult
from ownpi.subagent.coordinator import BatchResult
from ownpi.utils.io import write_text_atomic

if TYPE_CHECKING:
    from ownpi.runtime.rollout.completion_events import CompletionEvents


def project_batch_feedback(batch: BatchResult, events: 'CompletionEvents | None') -> BatchResult:
    if events is None:
        return batch
    root = events.store.path.parent.parent.resolve()
    children = []
    for child in batch.children:
        committed = events.committed_feedback(child.child_id)
        if committed is None:
            children.append(replace(child, feedback_submitted=False, feedback_path=None,
                                    feedback_source=None, feedback_error=None))
            continue
        state, source = committed
        child = replace(child, feedback_submitted=True, feedback_source=source,
                        result=SubagentResult(state.task_name, 'completed', state.completion_content),
                        feedback_path=None, feedback_error=None)
        key = sha256(child.child_id.encode('utf-8')).hexdigest()
        path = root / 'readback' / 'subagents' / key / 'completion.txt'
        try:
            _write_feedback(root, path, state.completion_content)
            child = replace(child, feedback_path=str(path))
        except (OSError, ValueError) as exc:
            child = replace(child, feedback_error=f'Feedback readback unavailable: {exc}')
        children.append(child)
    return replace(batch, children=tuple(children))


def _write_feedback(root: Path, path: Path, content: str) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate == root or not candidate.is_relative_to(root):
            continue
        if candidate.is_symlink() or (candidate.exists() and not (
            candidate.is_file() if candidate == path else candidate.is_dir()
        )):
            raise ValueError('Feedback readback requires regular files within the run')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Feedback readback is outside the run')
    if path.exists() and path.read_bytes() == content.encode('utf-8'):
        return
    write_text_atomic(path, content)
