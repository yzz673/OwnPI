"""Transient approval mailboxes for a live invocation, never execution replay."""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
from contextlib import ExitStack, contextmanager
import fcntl
import json
import logging
from pathlib import Path
from time import monotonic
from typing import Any, Iterator
from uuid import uuid4

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.runtime.events import now_utc_iso
from ownpi.schemas.runtime_permission import ToolPermissionSession
from ownpi.utils.io import write_json
from ownpi.utils.locking import filesystem_write_lock


class ApprovalCommunicationError(RuntimeError):
    """The live approval channel failed before the handler could start."""

    def __init__(self, operation: str, message: str) -> None:
        self.operation = operation
        super().__init__(f'Approval communication failed ({operation}): {message}')


def _mailbox_root(run_dir: Path) -> Path:
    return Path(run_dir) / 'runtime_records' / 'live_approvals'


@contextmanager
def approval_mailbox(
    run_dir: Path, session: ToolPermissionSession,
) -> Iterator[Path]:
    root = _mailbox_root(run_dir)
    path = root / f'{uuid4().hex}.json'
    answer = path.with_suffix('.answer')
    lease = path.with_suffix('.lease')
    lease_owner = ExitStack()
    try:
        try:
            root.mkdir(parents=True, exist_ok=True)
            lease_owner.enter_context(filesystem_write_lock(lease))
            write_json(path, {'session': session.to_dict()})
        except OSError as error:
            raise ApprovalCommunicationError('publish_request', str(error)) from error
        yield answer
    finally:
        # Release ownership even when cleanup fails; stale files are not live requests.
        try:
            lease_owner.close()
        except OSError:
            logging.getLogger(__name__).warning('Approval lease cleanup failed', exc_info=True)
        for leftover in (path, answer, lease):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning(
                    'Approval mailbox cleanup failed: %s', leftover, exc_info=True,
                )


def pending_live_approvals(run_dir: Path) -> tuple[tuple[Path, ToolPermissionSession], ...]:
    pending = []
    for path in _mailbox_root(run_dir).glob('*.json'):
        try:
            # The OS releases this invocation's lease even after a process crash.
            with path.with_suffix('.lease').open('rb') as lease:
                try:
                    fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    continue
            payload = json.loads(path.read_text(encoding='utf-8'))
            session = ToolPermissionSession.from_dict(payload['session'])
        except FileNotFoundError:
            continue
        if not path.with_suffix('.answer').exists():
            pending.append((path, session))
    return tuple(sorted(pending, key=lambda item: (item[1].created_at or '', item[1].session_id)))


def answer_live_approval(
    run_dir: Path, *, decision: str, scope: str, session_id: str = '',
) -> ToolPermissionSession | None:
    """Deliver a decision to the original waiter without taking the run writer lock."""
    if decision not in {'approve', 'deny', 'deny_command'}:
        raise ValueError(f'Unsupported permission decision: {decision}')
    if scope not in {'once', 'session'} or (decision != 'approve' and scope != 'once'):
        raise ValueError('Invalid permission decision scope')
    root = _mailbox_root(run_dir)
    if not root.is_dir():
        return None
    with filesystem_write_lock(root / '.answers.lock'):
        pending = pending_live_approvals(run_dir)
        selected = next((item for item in pending if not session_id or item[1].session_id == session_id), None)
        if selected is None:
            return None
        path, session = selected
        write_json(path.with_suffix('.answer'), {
            'session_id': session.session_id, 'decision': decision,
            'scope': scope, 'answered_at': now_utc_iso(),
        })
        return session


async def wait_for_approval(context: Any, session: ToolPermissionSession) -> ToolPermissionSession:
    store = context.state_store
    root = context.shared_resource_run_dir or context.run_dir
    waiting = False
    try:
        with store.live_tool_approval(session):
            with approval_mailbox(root, session) as answer_path:
                store.poll_runtime_control('tool_approval_waiting')
                waiting = True
                if callable(context.progress_callback):
                    try:
                        context.progress_callback({
                            'event': 'tool_permission_requested', 'session': session.to_dict(),
                        })
                    except (CancelledError, RuntimeControlRequested):
                        raise
                    except Exception:
                        logging.getLogger(__name__).warning('Approval progress notification failed', exc_info=True)
                retry_at = monotonic() + 0.25
                while True:
                    # A submitted answer wins a simultaneous cancellation.
                    answered = store.live_tool_approval_answer(session.session_id)
                    if answered is not None:
                        return answered
                    payload = _read_answer(answer_path, session.session_id)
                    if payload is not None:
                        return store.submit_tool_permission_decision(**payload)
                    if callable(context.cancel_checker) and context.cancel_checker():
                        raise CancelledError('Tool approval wait cancelled')
                    if monotonic() >= retry_at:
                        store.poll_runtime_control('tool_approval_record_retry')
                        retry_at = monotonic() + 0.25
                    await asyncio.sleep(0.05)
    finally:
        if waiting:
            store.poll_runtime_control('tool_approval_finished')


def _read_answer(path: Path, session_id: str) -> dict[str, str] | None:
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except OSError:
        logging.getLogger(__name__).warning('Approval answer read pending: %s', path, exc_info=True)
        return None
    except UnicodeError as error:
        raise ApprovalCommunicationError('read_answer', str(error)) from error
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise ApprovalCommunicationError('read_answer', str(error)) from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {'session_id', 'decision', 'scope', 'answered_at'}
        or not all(isinstance(value, str) for value in payload.values())
        or payload['session_id'] != session_id
        or payload['decision'] not in {'approve', 'deny', 'deny_command'}
        or payload['scope'] not in {'once', 'session'}
        or (payload['decision'] != 'approve' and payload['scope'] != 'once')
    ):
        raise ApprovalCommunicationError('read_answer', 'Invalid answer or mismatched invocation identity')
    return payload
