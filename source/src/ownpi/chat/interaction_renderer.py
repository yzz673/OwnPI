from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ownpi.runtime.payload_redaction import redact_payload


def _surface_is_developer(ui_mode: str) -> bool:
    return str(ui_mode or 'developer').strip().lower() == 'developer'


def human_interaction_kind(pending: dict[str, Any]) -> str:
    return (
        'human_question'
        if str(pending.get('origin_kind', 'controller') or 'controller') == 'tool'
        else 'human_clarification'
    )


def human_interaction_title(pending: dict[str, Any]) -> str:
    return 'Human question' if human_interaction_kind(pending) == 'human_question' else 'Human clarification'


def is_permission_interaction_payload(pending: dict[str, Any]) -> bool:
    if isinstance(pending.get('questions'), list):
        return False
    if str(pending.get('tool_name', '') or '').strip() == 'human_question':
        return False
    interaction_kind = str(pending.get('interaction_kind', '') or '').strip()
    if interaction_kind == 'tool_permission':
        return True
    return any(
        key in pending
        for key in ('request_id', 'permission_class', 'base_permission_class')
    )


def format_human_session_id(pending: dict[str, Any]) -> str:
    if str(pending.get('continuation_kind', 'stage_attempt_reproduce') or 'stage_attempt_reproduce') == 'direct_runtime':
        raw_session_id = str(pending.get('session_id', '<unknown>') or '<unknown>')
        suffix = raw_session_id.rsplit('-', 1)[-1] if '-' in raw_session_id else raw_session_id
        return f'direct-runtime-{suffix}' if suffix else 'direct-runtime'
    return str(pending.get('display_session_id') or pending.get('session_id') or '<unknown>')


def format_permission_session_id(pending: dict[str, Any]) -> str:
    if str(pending.get('continuation_kind', 'continuation_runtime') or 'continuation_runtime') == 'direct_runtime':
        raw_session_id = str(pending.get('session_id', '<unknown>') or '<unknown>')
        if ':' in raw_session_id:
            suffix = raw_session_id.rsplit(':', 1)[-1]
        else:
            suffix = raw_session_id.rsplit('-', 1)[-1] if '-' in raw_session_id else raw_session_id
        return f'direct-runtime-{suffix}' if suffix else 'direct-runtime'
    return str(pending.get('display_session_id') or pending.get('session_id') or '<unknown>')


def human_resume_target(pending: dict[str, Any]) -> str:
    display_target = str(pending.get('display_target', '') or '').strip()
    if display_target:
        return display_target
    if str(pending.get('continuation_kind', 'stage_attempt_reproduce') or 'stage_attempt_reproduce') == 'direct_runtime':
        return 'direct_runtime'
    return (
        f"{pending.get('stage', '<unknown>')} a{pending.get('attempt', 0)} "
        f"continuation={pending.get('continuation_index', 0)}"
    )


def permission_resume_target(pending: dict[str, Any]) -> str:
    display_target = str(pending.get('display_target', '') or '').strip()
    if display_target:
        return display_target
    if str(pending.get('continuation_kind', 'continuation_runtime') or 'continuation_runtime') == 'direct_runtime':
        return 'direct_runtime'
    return (
        f"{pending.get('stage', '<unknown>')} a{pending.get('attempt', 0)} "
        f"continuation={pending.get('continuation_index', 0)}"
    )


def permission_class_run_is_destructive(pending: dict[str, Any]) -> bool:
    permission_class = str(pending.get('permission_class', '') or '').strip().casefold()
    base_permission_class = str(pending.get('base_permission_class', '') or '').strip().casefold()
    return bool(pending.get('destructive_action', False)) or 'destructive' in {
        permission_class,
        base_permission_class,
    }


def clarification_answer_format_lines(*, include_cancel_hint: bool = False) -> list[str]:
    lines = [
        'Answer format: single-question prompts can be answered directly, or with `question_id: ...` / `question_key: ...`.',
        'Multi-question prompts accept `1: ...` / `question_id: ...` / `question_key: ...`. Multi-select answers may use comma-separated labels.',
    ]
    if include_cancel_hint:
        lines.append('Type `cancel` or `/cancel` to cancel this local capture; the run stays `paused_for_human`.')
    return lines


def render_pending_human_interaction(run_dir: Path, pending: dict[str, Any], *, ui_mode: str = 'developer') -> str:
    developer_surface = _surface_is_developer(ui_mode)
    lines = [
        f"# {human_interaction_title(pending)}",
        '',
        f"- waiting_for: {pending.get('waiting_for', 'answer questions')}",
    ]
    if developer_surface:
        lines.extend(
            [
                f'- run_dir: {run_dir}',
                f'- session_id: {format_human_session_id(pending)}',
                f"- origin_kind: {pending.get('origin_kind', 'controller')}",
                f"- interaction_request_id: {pending.get('interaction_request_id', pending.get('session_id', '<unknown>'))}",
                f"- resume_target: {human_resume_target(pending)} "
                f"resume={pending.get('continuation_kind', 'stage_attempt_reproduce')}",
            ]
        )
    else:
        lines.append(f'- run: {run_dir.name or str(run_dir)}')
    reason = str(pending.get('reason', '')).strip()
    if reason:
        lines.append(f'- reason: {reason}')
    actions = pending.get('available_actions', [])
    if isinstance(actions, list) and actions:
        lines.append(f"- available_actions: {', '.join(str(item) for item in actions)}")
    lines.extend(['', '## Questions'])
    for index, item in enumerate(pending.get('questions', []), start=1):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get('question_id', '?') or '?')
        question_key = str(item.get('question_key', question_id) or question_id)
        if developer_surface:
            lines.append(
                f"{index}. [{question_key}] "
                f"{item.get('header', '')}: {item.get('question', '')}"
            )
        else:
            lines.append(
                f"{index}. **{item.get('header', question_id)}** "
                f"[question_id: {question_id}] [question_key: {question_key}]: {item.get('question', '')}"
            )
        options = [option for option in item.get('options', []) if isinstance(option, dict)]
        if options:
            for option_index, option in enumerate(options, start=1):
                lines.append(
                    f"   - ({option_index}) {option.get('label', '')}: {option.get('description', '')}"
                )
                preview = str(option.get('preview', '') or '').strip()
                if preview:
                    lines.append(f'     preview: {preview}')
    lines.extend(['', *clarification_answer_format_lines(include_cancel_hint=True)])
    return '\n'.join(lines)


def render_pending_tool_permission(run_dir: Path, pending: dict[str, Any], *, ui_mode: str = 'developer') -> str:
    developer_surface = _surface_is_developer(ui_mode)
    waiting_for = str(pending.get('waiting_for') or f"approve {pending.get('tool_name', '<unknown>')}")
    lines = [
        '# Tool permission',
        '',
        f'- waiting_for: {waiting_for}',
        f"- tool_name: {pending.get('tool_name', '<unknown>')}",
        f"- permission_class: {pending.get('permission_class', 'default')}",
    ]
    if developer_surface:
        lines[2:2] = [
            f'- run_dir: {run_dir}',
            f"- session_id: {format_permission_session_id(pending)}",
            f"- origin_kind: {pending.get('origin_kind', 'runtime')}",
            f"- interaction_request_id: {pending.get('interaction_request_id', pending.get('request_id', '<unknown>'))}",
            f"- request_id: {pending.get('request_id', '<unknown>')}",
            f"- live_invocation: {permission_resume_target(pending)}",
            '- approval_delivery: answer the waiting invocation; no restart or replay',
        ]
    else:
        lines.insert(2, f'- run: {run_dir.name or str(run_dir)}')
    reason = str(pending.get('reason', '')).strip()
    if reason and developer_surface:
        lines.append(f'- reason: {reason}')
    human_prompt = str(pending.get('human_prompt', '')).strip()
    if human_prompt:
        lines.append(f'- prompt: {human_prompt}')
    input_digest = str(pending.get('input_digest', '')).strip()
    if input_digest:
        lines.append(f'- input: {input_digest}')
    lines.extend(f'- {line}' for line in permission_request_detail_lines(pending))
    if permission_class_run_is_destructive(pending):
        lines.append(
            '- warning: `approve session` allows matching requests in this run/session '
            'without asking again.'
        )
    actions = pending.get('available_actions', [])
    if isinstance(actions, list) and actions:
        lines.append(f"- available_actions: {', '.join(str(item) for item in actions)}")
    lines.extend(
        [
            '',
            'The main path opens the focused permission dialog.',
            'Text fallback accepts `approve`, `approve session`, `deny command`, or `deny`.',
        ]
    )
    return '\n'.join(lines)


def permission_request_detail_lines(pending: dict[str, Any]) -> list[str]:
    approval_request = (
        pending.get('approval_request', {})
        if isinstance(pending.get('approval_request'), dict)
        else {}
    )
    request_payload = (
        pending.get('request_payload', {})
        if isinstance(pending.get('request_payload'), dict)
        else {}
    )
    request_input = (
        request_payload.get('input', {})
        if isinstance(request_payload.get('input'), dict)
        else {}
    )
    lines: list[str] = []
    action = str(approval_request.get('action', '') or '').strip()
    if action:
        lines.append(f'action: {action}')
    if request_input:
        rendered_input = json.dumps(
            redact_payload(request_input, max_depth=5, max_keys=32),
            ensure_ascii=True,
            sort_keys=True,
            separators=(',', ':'),
            default=str,
        )
        if len(rendered_input) > 800:
            rendered_input = f'{rendered_input[:797]}...'
        lines.append(f'request input: {rendered_input}')
    overlay = (
        approval_request.get('requested_overlay', {})
        if isinstance(approval_request.get('requested_overlay'), dict)
        else {}
    )
    entries = overlay.get('file_system_entries', [])
    if isinstance(entries, list):
        for entry in entries[:8]:
            if not isinstance(entry, dict):
                continue
            path_payload = entry.get('path', {})
            path = (
                str(path_payload.get('value', '') or '').strip()
                if isinstance(path_payload, dict)
                else ''
            )
            access = str(entry.get('access', '') or '').strip()
            if path:
                lines.append(f'canonical target: {access or "access"} {path}')
    if overlay.get('network_enabled') is True:
        lines.append('network escalation: enabled')
    return lines
