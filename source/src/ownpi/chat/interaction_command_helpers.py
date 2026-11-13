from __future__ import annotations

from pathlib import Path
from typing import Any

from ownpi.chat.answer_normalizer import (
    normalize_human_answer_mapping,
    parse_human_answers_text,
)
from ownpi.chat.interaction_renderer import (
    permission_class_run_is_destructive as _permission_class_run_is_destructive,
    render_pending_human_interaction,
    render_pending_tool_permission,
)
from ownpi.chat.permission_dialog import PermissionDialogResult
from ownpi.controller.pending_interactions import (
    pending_human_interactions,
    pending_permission_interactions,
)


def pending_human_clarification_for_run(run_dir: Path) -> dict[str, Any]:
    pending = pending_human_interactions(run_dir)
    return pending[0] if pending else {}


def pending_tool_permission_for_run(run_dir: Path) -> dict[str, Any]:
    pending = pending_permission_interactions(run_dir)
    return pending[0] if pending else {}


def render_pending_human_clarification(
    run_dir: Path,
    pending: dict[str, Any],
    *,
    ui_mode: str = 'developer',
) -> str:
    return render_pending_human_interaction(run_dir, pending, ui_mode=ui_mode)


def render_pending_tool_permission_panel(
    run_dir: Path,
    pending: dict[str, Any],
    *,
    ui_mode: str = 'developer',
) -> str:
    return render_pending_tool_permission(run_dir, pending, ui_mode=ui_mode)


def permission_class_run_is_destructive(pending: dict[str, Any]) -> bool:
    return _permission_class_run_is_destructive(pending)


def human_dialog_emergency_fallback_message() -> str:
    return 'Focused question dialog is unavailable. Entering emergency text fallback mode.'


def is_clarify_capture_cancel_command(message: str) -> bool:
    normalized = str(message or '').strip().lower()
    return normalized in {'cancel', '/cancel', '/cancel-clarify'}


def is_permission_capture_cancel_command(message: str) -> bool:
    normalized = str(message or '').strip().lower()
    return normalized in {'cancel', '/cancel', '/cancel-permission'}


def answer_payload_has_value(payload: dict[str, Any]) -> bool:
    selected = payload.get('selected_labels', [])
    return bool(selected) or bool(str(payload.get('free_text', '') or '').strip())


def parse_clarification_answers(
    message: str,
    pending: dict[str, Any],
    *,
    draft_answers_by_question_id: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    parsed = parse_human_answers_text(message, pending=pending)
    if not draft_answers_by_question_id:
        return parsed
    normalized_drafts = normalize_human_answer_mapping(
        draft_answers_by_question_id,
        pending=pending,
    )
    merged = {
        question_id: payload
        for question_id, payload in normalized_drafts.items()
        if answer_payload_has_value(payload)
    }
    merged.update(parsed)
    return merged


def parse_tool_permission_response(message: str) -> PermissionDialogResult:
    normalized = str(message or '').strip().lower()
    compact = normalized.replace('-', '_').replace(' ', '_')
    if compact in {'approve', 'approved', 'yes', 'y', 'approve_once', 'approved_once'}:
        return PermissionDialogResult(decision='approve', scope='once')
    if compact in {'deny', 'denied', 'no', 'n'}:
        return PermissionDialogResult(decision='deny', scope='once')
    if compact in {
        'deny_command',
        'deny_current',
        'deny_current_command',
        'reject_command',
        'reject_current_command',
    }:
        return PermissionDialogResult(decision='deny_command', scope='once')
    if compact in {'approve_session', 'allow_session', 'session'}:
        return PermissionDialogResult(decision='approve', scope='session')
    raise ValueError(
        'Failed to parse tool permission decision. Please reply with `approve`, `deny`, '
        '`deny command`, or `approve session`.'
    )


def parse_tool_permission_decision(message: str) -> str:
    return parse_tool_permission_response(message).decision
