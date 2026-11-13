from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ownpi.chat.interaction_dialog import (
    DIALOG_FALLBACK,
    _dialog_style_rules,
    _format_header,
    interaction_dialog_available,
    resolve_permission_dialog_viewport,
)
from ownpi.chat.interaction_renderer import permission_request_detail_lines


@dataclass(frozen=True)
class PermissionDialogResult:
    decision: str
    scope: str = 'once'


def run_permission_dialog(
    *,
    run_dir: Path,
    pending: dict[str, Any],
    ui_mode: str = 'developer',
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> PermissionDialogResult | object | None:
    if not interaction_dialog_available():
        return None
    viewport = resolve_permission_dialog_viewport()
    if not viewport.dialog_capable:
        return DIALOG_FALLBACK
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, VSplit
        from prompt_toolkit.styles import Style
        from prompt_toolkit.widgets import Box, Button, Dialog, Frame, Label, RadioList, TextArea
    except ImportError:
        return None

    tool_name = str(pending.get('tool_name', '<unknown>') or '<unknown>')
    permission_class = str(pending.get('permission_class', 'default') or 'default')
    actions = _permission_action_values()
    action_list = RadioList(values=[(item[0], item[1]) for item in actions], default=actions[0][0])
    result: PermissionDialogResult | None = None

    def _confirm() -> None:
        nonlocal result
        action = str(action_list.current_value or 'approve_once')
        decision, scope = _permission_action_to_response(action)
        result = PermissionDialogResult(decision=decision, scope=scope)
        app.exit(result=result)

    def _cancel() -> None:
        app.exit(result=None)

    lines = [
        *_format_header(
            title=f'waiting for approve {tool_name}',
            run_dir=run_dir,
            pending=pending,
            ui_mode=ui_mode,
        ),
        '',
        f'tool name: {tool_name}',
        f'permission class: {permission_class}',
    ]
    if str(ui_mode or 'developer').strip().lower() == 'developer':
        lines.append(f'status: {pending.get("status", "pending")}')
    if bool(pending.get('destructive_action', False)):
        lines.append('destructive: true')
        destructive_reason = str(pending.get('destructive_reason', '') or '').strip()
        if destructive_reason:
            lines.append(f'destructive reason: {destructive_reason}')
    prompt = str(pending.get('human_prompt', '') or '').strip()
    reason = str(pending.get('reason', '') or '').strip()
    input_digest = str(pending.get('input_digest', '') or '').strip()
    detail_lines = [*lines, '']
    if prompt:
        detail_lines.append(f'prompt: {prompt}')
    if reason and str(ui_mode or 'developer').strip().lower() == 'developer':
        detail_lines.append(f'reason: {reason}')
    if input_digest:
        detail_lines.append(f'input digest: {input_digest}')
    detail_lines.extend(permission_request_detail_lines(pending))
    detail_lines.extend(
        [
            '',
            'Scoped allow decisions affect only this run/session, not global settings.',
            (
                'Esc closes this dialog; the live invocation still needs an answer.'
                if str(ui_mode or 'developer').strip().lower() == 'developer'
                else 'Esc closes this dialog; approval is still waiting.'
            ),
        ]
    )
    detail_text = '\n'.join(detail_lines)
    body = HSplit(
        [
            Frame(
                TextArea(
                    text=detail_text,
                    read_only=True,
                    scrollbar=True,
                    height=min(14, viewport.body_height),
                ),
                title='Permission request',
            ),
            Label(text=''),
            Frame(action_list, title='Choose action'),
            Label(text=''),
            VSplit([Button(text='Confirm', handler=_confirm), Button(text='Cancel', handler=_cancel)], padding=1),
        ]
    )
    key_bindings = KeyBindings()

    @key_bindings.add('escape')
    def _(event) -> None:
        event.app.exit(result=None)

    app = Application(
        layout=Layout(
            Box(
                Dialog(
                    title='OwnPI · Tool permission',
                    body=body,
                    buttons=[],
                    width=viewport.width,
                    modal=True,
                ),
                padding=1,
            )
        ),
        key_bindings=key_bindings,
        style=Style.from_dict(
            _dialog_style_rules(
                theme_setting=theme_setting,
                use_terminal_background=use_terminal_background,
            )
        ),
        full_screen=False,
    )
    try:
        return app.run()
    except Exception:
        return DIALOG_FALLBACK


def _permission_action_values() -> list[tuple[str, str]]:
    return [
        ('approve_once', 'Approve once'),
        ('approve_session', 'Approve for this session'),
        ('deny_command', 'Deny current command and continue'),
        ('deny', 'Deny required execution'),
    ]


def _permission_action_to_response(action: str) -> tuple[str, str]:
    normalized = str(action or '').strip().lower()
    if normalized == 'deny_command':
        return 'deny_command', 'once'
    if normalized == 'deny':
        return 'deny', 'once'
    if normalized == 'approve_session':
        return 'approve', 'session'
    return 'approve', 'once'
