from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sys
import unicodedata
from typing import Any, Callable, Literal

from ownpi.chat.answer_normalizer import normalize_human_answer_mapping
from ownpi.chat.interaction_renderer import (
    format_human_session_id,
    format_permission_session_id,
    human_resume_target,
    is_permission_interaction_payload,
    permission_resume_target,
)
from ownpi.chat.interaction_session import HumanQuestionSession
from ownpi.chat.theme import (
    build_prompt_toolkit_dialog_style,
    get_ui_tokens,
    resolve_theme_setting,
)
from ownpi.schemas.runtime_human_clarification import human_answer_has_value, human_answer_text


EMPTY_SENTINEL = '__ownpi_no_selection__'
OTHER_SENTINEL = '__ownpi_other__'
DIALOG_FALLBACK = object()

_FOOTER_CHAT_LABEL = 'Chat about this'


@dataclass(frozen=True)
class HumanDialogFallbackResult:
    draft_answers_by_question_id: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class DialogViewport:
    columns: int
    lines: int
    width: int
    body_height: int
    wide_layout: bool
    preview_eligible: bool
    dialog_capable: bool


def _resolve_terminal_size(*, columns: int | None = None, lines: int | None = None) -> tuple[int, int]:
    terminal_size = shutil.get_terminal_size(fallback=(100, 28))
    resolved_columns = int(columns if columns is not None else terminal_size.columns)
    resolved_lines = int(lines if lines is not None else terminal_size.lines)
    return resolved_columns, resolved_lines


def resolve_human_dialog_viewport(*, columns: int | None = None, lines: int | None = None) -> DialogViewport:
    resolved_columns, resolved_lines = _resolve_terminal_size(columns=columns, lines=lines)
    return DialogViewport(
        columns=resolved_columns,
        lines=resolved_lines,
        width=resolved_columns,
        body_height=resolved_lines,
        wide_layout=resolved_columns >= 60 and resolved_lines >= 20,
        preview_eligible=resolved_columns >= 60 and resolved_lines >= 20,
        dialog_capable=resolved_columns >= 44 and resolved_lines >= 16,
    )


def resolve_permission_dialog_viewport(*, columns: int | None = None, lines: int | None = None) -> DialogViewport:
    resolved_columns, resolved_lines = _resolve_terminal_size(columns=columns, lines=lines)
    width = min(max(resolved_columns - 10, 72), 110)
    body_height = max(12, resolved_lines - 12)
    dialog_capable = resolved_columns >= 96 and resolved_lines >= 28
    return DialogViewport(
        columns=resolved_columns,
        lines=resolved_lines,
        width=width,
        body_height=body_height,
        wide_layout=resolved_columns >= 110,
        preview_eligible=False,
        dialog_capable=dialog_capable,
    )


def focused_dialog_fallback_message(*, kind: str) -> str:
    label = 'question' if kind == 'human_question' else 'permission'
    return f'Terminal is too narrow for the focused {label} dialog. Entering emergency text fallback mode.'


@dataclass
class _QuestionAnswerState:
    selected_labels: list[str] = field(default_factory=list)
    free_text: str = ''

    def to_payload(self) -> dict[str, Any]:
        return {
            'selected_labels': list(self.selected_labels),
            'free_text': self.free_text.strip() or None,
        }


@dataclass(frozen=True)
class _DialogChoice:
    kind: Literal['option', 'input', 'chat']
    label: str
    description: str = ''
    value: str = ''
    preview: str | None = None


FocusRegion = Literal['options', 'footer', 'submit_actions']


@dataclass(frozen=True)
class _FocusTransition:
    region: FocusRegion
    index: int


EditorFocusKind = Literal['other_input', 'note_input']


@dataclass(frozen=True)
class _EditorFocusState:
    kind: EditorFocusKind
    question_id: str
    option_label: str | None = None


_SHIFT_ENTER_INTERNAL_KEY = 'f24'
_SHIFT_ENTER_SEQUENCES = (
    '\x1b[13;2u',
    '\x1b[27;2;13~',
)
_MACOS_SHIFT_MASK = 1 << 17
_ESCAPE_FLUSH_TIMEOUT_SECONDS = 0.03
_KEY_SEQUENCE_TIMEOUT_SECONDS = 0.05


def interaction_dialog_available() -> bool:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        return False
    return True


def _real_option_choices(question: dict[str, Any]) -> list[_DialogChoice]:
    choices: list[_DialogChoice] = []
    for option in question.get('options', []):
        if not isinstance(option, dict):
            continue
        label = str(option.get('label', '') or '').strip()
        if not label:
            continue
        choices.append(
            _DialogChoice(
                kind='option',
                label=label,
                description=str(option.get('description', '') or '').strip(),
                value=label,
                preview=(str(option.get('preview', '') or '').strip() or None),
            )
        )
    return choices


def _uses_preview_layout(question: dict[str, Any]) -> bool:
    if bool(question.get('multi_select', False)):
        return False
    return any(choice.preview for choice in _real_option_choices(question))


def _should_use_preview_layout(question: dict[str, Any]) -> bool:
    """Claude-style auto-preview for single-select questions with previews."""

    return _uses_preview_layout(question)


def _input_placeholder(question: dict[str, Any]) -> str:
    return 'Type something' if bool(question.get('multi_select', False)) else 'Type something.'


def _dialog_choices(question: dict[str, Any]) -> list[_DialogChoice]:
    choices = list(_real_option_choices(question))
    if _should_use_preview_layout(question):
        return choices
    choices.append(_DialogChoice(kind='input', label=_input_placeholder(question), value=OTHER_SENTINEL))
    return choices


def _hide_submit_tab(questions: list[dict[str, Any]]) -> bool:
    return len(questions) == 1 and not bool(questions[0].get('multi_select', False))


def _submit_row_label(*, current_question_index: int, questions: list[dict[str, Any]]) -> str:
    return 'Next' if current_question_index < len(questions) - 1 else 'Submit'


def _human_help_text(
    *,
    columns: int,
    question_count: int,
    is_multi_select: bool,
    notes_available: bool = False,
    on_submit_screen: bool = False,
    editor_mode: str | None = None,
) -> list[str]:
    del editor_mode, is_multi_select, question_count
    if on_submit_screen:
        return _wrap_plain('enter to submit answer | esc to interrupt', columns)
    actions: list[str] = []
    if notes_available:
        actions.append('tab to add notes')
    actions.extend(['enter to submit answer', 'esc to interrupt'])
    return _wrap_plain(' | '.join(actions), columns)


def _question_answered(question_id: str, session: HumanQuestionSession) -> bool:
    return human_answer_has_value(session.answer_payload_for(question_id))


def _default_cursor_index(question: dict[str, Any], session: HumanQuestionSession) -> int:
    question_id = str(question.get('question_id', '') or '').strip()
    choices = _dialog_choices(question)
    if not choices:
        return 0
    draft = session.draft_for(question_id)
    if draft.selected_labels:
        selected = draft.selected_labels[0]
        for index, choice in enumerate(choices):
            if choice.kind == 'option' and choice.value == selected:
                return index
    if str(draft.free_text or '').strip():
        for index, choice in enumerate(choices):
            if choice.kind == 'input':
                return index
    return 0


def _question_supports_option_notes(question: dict[str, Any] | None) -> bool:
    if not isinstance(question, dict):
        return False
    if bool(question.get('multi_select', False)):
        return False
    return bool(_real_option_choices(question))


def _display_width(text: str) -> int:
    width = 0
    for char in str(text or ''):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {'F', 'W'} else 1
    return width


def _pad_to_width(text: str, width: int) -> str:
    return text + (' ' * max(0, width - _display_width(text)))


def _truncate_to_width(text: str, width: int) -> str:
    normalized = str(text or '')
    if width <= 0:
        return ''
    if _display_width(normalized) <= width:
        return normalized
    if width == 1:
        return '…'
    output: list[str] = []
    used = 0
    budget = width - 1
    for char in normalized:
        char_width = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in {'F', 'W'} else 1)
        if used + char_width > budget:
            break
        output.append(char)
        used += char_width
    return ''.join(output) + '…'


def _truncate_fragments_to_width(
    fragments: list[tuple[str, str]],
    width: int,
) -> list[tuple[str, str]]:
    width = max(0, int(width))
    if width <= 0:
        return []
    output: list[tuple[str, str]] = []
    used = 0
    for style, text in fragments:
        remaining = width - used
        if remaining <= 0:
            break
        text_width = _display_width(text)
        if text_width <= remaining:
            output.append((style, text))
            used += text_width
            continue
        truncated = _truncate_to_width(text, remaining)
        if truncated:
            output.append((style, truncated))
        break
    return output


def _navigation_tab_labels(
    *,
    questions: list[dict[str, Any]],
    current_index: int,
    width: int,
    hide_submit_tab: bool = False,
) -> list[str]:
    submit_text = '' if hide_submit_tab else ' ✔ Submit '
    fixed_width = _display_width('← ') + _display_width(' →') + _display_width(submit_text)
    available = max(0, width - fixed_width)
    headers = [str(q.get('header', '') or '').strip() or f'Q{idx + 1}' for idx, q in enumerate(questions)]
    if not headers:
        return []
    if available <= 0:
        return [header[:3] if index == current_index else '' for index, header in enumerate(headers)]
    ideal_widths = [4 + _display_width(header) for header in headers]
    if sum(ideal_widths) <= available:
        return headers
    current_header = headers[current_index] if 0 <= current_index < len(headers) else ''
    current_ideal_width = 4 + _display_width(current_header)
    current_tab_width = min(current_ideal_width, max(4, available // 2))
    remaining_width = max(0, available - current_tab_width)
    other_count = max(1, len(headers) - 1)
    width_per_other = max(6, remaining_width // other_count)
    labels: list[str] = []
    for index, header in enumerate(headers):
        max_text_width = (current_tab_width if index == current_index else width_per_other) - 4
        labels.append(_truncate_to_width(header, max(1, int(max_text_width))))
    return labels


def _navigation_bar_fragments(
    *,
    questions: list[dict[str, Any]],
    session: HumanQuestionSession,
    current_index: int,
    width: int,
    hide_submit_tab: bool,
) -> list[tuple[str, str]]:
    labels = _navigation_tab_labels(
        questions=questions,
        current_index=min(current_index, max(0, len(questions) - 1)),
        width=width,
        hide_submit_tab=hide_submit_tab,
    )
    hide_arrows = len(questions) == 1 and hide_submit_tab
    line: list[tuple[str, str]] = []
    if not hide_arrows:
        line.append(('class:ask.nav.arrow', '← '))
    for index, question in enumerate(questions):
        question_id = str(question.get('question_id', '') or '').strip()
        marker = '☑' if _question_answered(question_id, session) else '☐'
        label = labels[index] if index < len(labels) else f'Q{index + 1}'
        style = 'class:ask.nav.current' if index == current_index else 'class:ask.nav.inactive'
        line.append((style, f' {marker} {label} '))
    if not hide_submit_tab:
        style = 'class:ask.nav.current' if current_index == len(questions) else 'class:ask.nav.inactive'
        line.append((style, ' ✔ Submit '))
    if not hide_arrows:
        line.append(('class:ask.nav.arrow', ' →'))
    return _truncate_fragments_to_width(line, max(1, width))


def _navigation_bar_text(
    *,
    questions: list[dict[str, Any]],
    session: HumanQuestionSession,
    current_index: int,
) -> str:
    return _navigation_bar_plain(
        questions=questions,
        session=session,
        current_index=current_index,
        width=shutil.get_terminal_size(fallback=(100, 28)).columns,
        hide_submit_tab=_hide_submit_tab(questions),
    )


def _navigation_bar_plain(
    *,
    questions: list[dict[str, Any]],
    session: HumanQuestionSession,
    current_index: int,
    width: int,
    hide_submit_tab: bool,
) -> str:
    fragments = _navigation_bar_fragments(
        questions=questions,
        session=session,
        current_index=current_index,
        width=width,
        hide_submit_tab=hide_submit_tab,
    )
    return ''.join(text for _, text in fragments)


def _next_focus_from_down(*, region: FocusRegion, index: int, option_count: int) -> _FocusTransition:
    if region == 'footer':
        return _FocusTransition('footer', 0)
    if region == 'submit_actions':
        return _FocusTransition('submit_actions', min(index + 1, 1))
    if index < max(0, option_count - 1):
        return _FocusTransition('options', index + 1)
    return _FocusTransition('footer', 0)


def _previous_focus_from_up(*, region: FocusRegion, index: int, option_count: int) -> _FocusTransition:
    if region == 'footer':
        return _FocusTransition('options', max(0, option_count - 1))
    if region == 'submit_actions':
        return _FocusTransition('submit_actions', max(0, index - 1))
    return _FocusTransition('options', max(0, index - 1))


def _sync_single_select_answer(
    *,
    session: HumanQuestionSession,
    question_id: str,
    selected_value: str | None,
    free_text: str | None,
) -> dict[str, Any]:
    normalized_value = str(selected_value or '').strip()
    trimmed_free_text = str(free_text or '').strip() or None
    session.update_single_select(
        question_id,
        None if normalized_value in {EMPTY_SENTINEL, OTHER_SENTINEL, ''} else normalized_value,
    )
    session.update_free_text(
        question_id,
        trimmed_free_text if normalized_value == OTHER_SENTINEL else None,
    )
    return session.answer_payload_for(question_id)


def _sync_multi_select_answer(
    *,
    session: HumanQuestionSession,
    question_id: str,
    selected_values: list[str],
    free_text: str | None,
) -> dict[str, Any]:
    normalized_values = [str(item or '').strip() for item in selected_values]
    session.update_multi_select(
        question_id,
        [item for item in normalized_values if item and item != OTHER_SENTINEL],
    )
    session.update_free_text(
        question_id,
        (str(free_text or '').strip() or None) if OTHER_SENTINEL in normalized_values else None,
    )
    return session.answer_payload_for(question_id)


def _clear_custom_input_after_option_change(
    *,
    session: HumanQuestionSession,
    question_id: str,
    input_buffer: Any | None = None,
) -> None:
    if input_buffer is not None:
        reset = getattr(input_buffer, 'reset', None)
        if callable(reset):
            reset()
        elif hasattr(input_buffer, 'text'):
            input_buffer.text = ''
    session.update_free_text(question_id, None)


def _build_auto_focus_radio_list(
    *,
    radio_list_cls: type[Any],
    values: list[tuple[str, str]],
    default: str,
    on_value_selected: Callable[[str], None] | None = None,
) -> Any:
    from prompt_toolkit.keys import Keys

    class _AutoFocusRadioList(radio_list_cls):
        def __init__(
            self,
            *args: Any,
            on_value_selected: Callable[[str], None] | None = None,
            **kwargs: Any,
        ) -> None:
            self._on_value_selected = on_value_selected
            super().__init__(*args, **kwargs)
            for key in ('up', 'down', 'k', 'j'):
                try:
                    self.control.key_bindings.remove(key)
                except ValueError:
                    continue
            move_bindings = self.control.key_bindings

            def _move(delta: int) -> None:
                previous_index = self._selected_index
                self._selected_index = min(
                    len(self.values) - 1,
                    max(0, self._selected_index + delta),
                )
                if self._selected_index != previous_index:
                    self._handle_enter()

            @move_bindings.add('up')
            @move_bindings.add('k')
            def _move_up(event: Any) -> None:
                del event
                _move(-1)

            @move_bindings.add('down')
            @move_bindings.add('j')
            def _move_down(event: Any) -> None:
                del event
                _move(1)

            @move_bindings.add(Keys.Up)
            def _move_up_arrow(event: Any) -> None:
                del event
                _move(-1)

            @move_bindings.add(Keys.Down)
            def _move_down_arrow(event: Any) -> None:
                del event
                _move(1)

        def _handle_enter(self) -> None:
            super()._handle_enter()
            if self._on_value_selected is not None:
                self._on_value_selected(str(self.current_value or ''))

    return _AutoFocusRadioList(
        values=values,
        default=default,
        select_on_focus=True,
        on_value_selected=on_value_selected,
    )


def _validate_dialog_answers(
    questions: list[dict[str, Any]],
    answers: dict[str, _QuestionAnswerState],
) -> list[str]:
    errors: list[str] = []
    for index, question in enumerate(questions, start=1):
        question_id = str(question.get('question_id', '') or '').strip()
        if not question_id or not bool(question.get('required', True)):
            continue
        payload = answers.get(question_id, _QuestionAnswerState()).to_payload()
        if human_answer_has_value(payload):
            continue
        header = str(question.get('header', '') or '').strip() or question_id
        errors.append(f'Question {index} ({header}) requires an answer.')
    return errors


def _format_header(
    *,
    title: str,
    run_dir: Path,
    pending: dict[str, Any],
    ui_mode: str = 'developer',
) -> list[Any]:
    if str(ui_mode or 'developer').strip().lower() != 'developer':
        return [title, '', f'Run: {run_dir.name or str(run_dir)}']

    is_permission = is_permission_interaction_payload(pending)
    session_id = (
        format_permission_session_id(pending)
        if is_permission
        else format_human_session_id(pending)
    )
    interaction_request_id = str(
        pending.get('interaction_request_id')
        or pending.get('request_id')
        or pending.get('session_id')
        or '<unknown>'
    )
    origin_kind = str(pending.get('origin_kind', 'runtime') or 'runtime')
    resume_target = (
        permission_resume_target(pending)
        if is_permission
        else human_resume_target(pending)
    )
    return [
        title,
        '',
        f'run_dir: {run_dir}',
        f'session_id: {session_id}',
        f'origin_kind: {origin_kind}',
        f'interaction_request_id: {interaction_request_id}',
        f'resume_target: {resume_target}',
    ]


def _dialog_style_rules(
    *,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> dict[str, str]:
    theme_name = resolve_theme_setting(theme_setting)
    tokens = get_ui_tokens(theme_name, use_terminal_background=use_terminal_background)
    rules = build_prompt_toolkit_dialog_style(tokens)
    # In auto/light terminals, rely on terminal-default foreground for primary text
    # to avoid accidental low-contrast gray when theme detection is imperfect.
    default_fg = 'fg:default'
    muted_fg = '#6b7280' if use_terminal_background else tokens.text_muted
    placeholder_fg = muted_fg
    rules.update(
        {
            'ask': default_fg,
            'ask.divider': f'fg:{tokens.border}',
            'ask.nav.current': f'fg:#ffffff bg:{tokens.accent_soft} bold',
            'ask.nav.inactive': default_fg,
            'ask.nav.arrow': f'fg:{muted_fg}',
            'ask.pointer': f'fg:{tokens.accent} bold',
            'ask.option.index': f'fg:{muted_fg}',
            'ask.option.label': default_fg,
            'ask.option.focused': f'fg:{tokens.accent} bold',
            'ask.option.selected': f'fg:{tokens.success} bold',
            'ask.option.description': f'fg:{muted_fg}',
            'ask.option.description.focused': f'fg:{muted_fg}',
            'ask.option.description.selected': f'fg:{muted_fg}',
            'ask.input': default_fg,
            'ask.input.placeholder': f'fg:{placeholder_fg}',
            'ask.input.cursor': default_fg,
            'ask.footer': default_fg,
            'ask.help': f'fg:{muted_fg}',
            'ask.title': f'{default_fg} bold',
            'ask.warning': f'fg:{tokens.warning} bold',
            'ask.error': f'fg:{tokens.error} bold',
            'ask.preview.border': f'fg:{tokens.border}',
            'ask.preview.text': default_fg,
            'ask.review.answer': default_fg,
        }
    )
    return rules


def _wrap_plain(text: str, width: int) -> list[str]:
    width = max(1, int(width))
    normalized = str(text or '').strip()
    if not normalized:
        return ['']
    wrapped: list[str] = []
    for paragraph in normalized.splitlines() or ['']:
        stripped = paragraph.strip()
        if not stripped:
            wrapped.append('')
            continue
        cursor = 0
        length = len(stripped)
        while cursor < length:
            used = 0
            end = cursor
            last_break: int | None = None
            while end < length:
                char = stripped[end]
                char_width = _display_width(char)
                if used + char_width > width:
                    break
                used += char_width
                if char.isspace():
                    last_break = end
                end += 1
            if end >= length:
                line = stripped[cursor:end].strip()
                if line:
                    wrapped.append(line)
                break
            if last_break is not None and last_break >= cursor:
                line = stripped[cursor:last_break].rstrip()
                next_cursor = last_break + 1
                while next_cursor < length and stripped[next_cursor].isspace():
                    next_cursor += 1
            else:
                if end == cursor:
                    end = min(length, cursor + 1)
                line = stripped[cursor:end].rstrip()
                next_cursor = end
            if line:
                wrapped.append(line)
            cursor = next_cursor
    return wrapped or ['']


def _wrap_plain_with_widths(text: str, *, first_width: int, continuation_width: int) -> list[str]:
    first_width = max(1, int(first_width))
    continuation_width = max(1, int(continuation_width))
    normalized = str(text or '').strip()
    if not normalized:
        return ['']
    wrapped: list[str] = []
    first_line = True
    for paragraph in normalized.splitlines() or ['']:
        stripped = paragraph.strip()
        if not stripped:
            wrapped.append('')
            first_line = False
            continue
        cursor = 0
        length = len(stripped)
        while cursor < length:
            width = first_width if first_line else continuation_width
            used = 0
            end = cursor
            last_break: int | None = None
            while end < length:
                char = stripped[end]
                char_width = _display_width(char)
                if used + char_width > width:
                    break
                used += char_width
                if char.isspace():
                    last_break = end
                end += 1
            if end >= length:
                line = stripped[cursor:end].strip()
                if line:
                    wrapped.append(line)
                    first_line = False
                break
            if last_break is not None and last_break >= cursor:
                line = stripped[cursor:last_break].rstrip()
                next_cursor = last_break + 1
                while next_cursor < length and stripped[next_cursor].isspace():
                    next_cursor += 1
            else:
                if end == cursor:
                    end = min(length, cursor + 1)
                line = stripped[cursor:end].rstrip()
                next_cursor = end
            if line:
                wrapped.append(line)
                first_line = False
            cursor = next_cursor
    return wrapped or ['']


def _rendered_line_display_width(parts: list[tuple[str, str]]) -> int:
    return sum(_display_width(text) for _, text in parts)


def _wrapped_text_fragments(
    *,
    style: str,
    text: str,
    content_width: int,
    first_prefix: list[tuple[str, str]] | None = None,
    continuation_prefix: list[tuple[str, str]] | None = None,
    suffix: list[tuple[str, str]] | None = None,
) -> list[list[tuple[str, str]]]:
    first_prefix = list(first_prefix or [])
    continuation_prefix = list(continuation_prefix if continuation_prefix is not None else first_prefix)
    suffix = list(suffix or [])
    prefix_width = _rendered_line_display_width(first_prefix)
    continuation_width = _rendered_line_display_width(continuation_prefix)
    suffix_width = _rendered_line_display_width(suffix)
    first_budget = max(1, content_width - prefix_width - suffix_width)
    continuation_budget = max(1, content_width - continuation_width)
    chunks = _wrap_plain_with_widths(
        text,
        first_width=first_budget,
        continuation_width=continuation_budget,
    )
    lines: list[list[tuple[str, str]]] = []
    for index, chunk in enumerate(chunks):
        prefix = first_prefix if index == 0 else continuation_prefix
        line = [*prefix, (style, chunk)]
        if index == 0 and suffix:
            line.extend(suffix)
        lines.append(line)
    return lines or [[(style, '')]]


def _divider_line(width: int) -> list[tuple[str, str]]:
    return [('class:ask.divider', '─' * max(0, width))]


def _choice_display_lines(
    *,
    choice: _DialogChoice,
    number: int,
    is_focused: bool,
    is_selected: bool,
    is_multi_select: bool,
    input_text: str,
    max_index_width: int,
    content_width: int,
) -> list[list[tuple[str, str]]]:
    pointer = '❯' if is_focused else ' '
    prefix_style = 'class:ask.pointer' if is_focused else 'class:ask'
    index_field = _pad_to_width(f'{number}.', max_index_width) + ' '
    continuation_style = 'class:ask'
    if choice.kind == 'input':
        has_input = bool(str(input_text or '').strip())
        label = input_text if is_focused and has_input else (input_text or choice.label)
        input_style = (
            'class:ask.input'
            if not is_focused
            else ('class:ask.input' if has_input else 'class:ask.input.placeholder')
        )
        first_prefix: list[tuple[str, str]] = [
            (prefix_style, f'{pointer} '),
            ('class:ask.option.index', index_field),
        ]
        continuation_prefix = [(continuation_style, ' ' * _rendered_line_display_width(first_prefix))]
        if is_focused and not has_input:
            first_budget = max(1, content_width - _rendered_line_display_width(first_prefix) - 1)
            placeholder_tail = label[1:] if label else ''
            wrapped_tail = _wrap_plain(placeholder_tail, first_budget) if placeholder_tail else ['']
            first_line: list[tuple[str, str]] = [*first_prefix, ('class:ask.input.cursor', '█')]
            if wrapped_tail and wrapped_tail[0]:
                first_line.append((input_style, wrapped_tail[0]))
            lines: list[list[tuple[str, str]]] = [first_line]
            for extra in wrapped_tail[1:]:
                lines.append([*continuation_prefix, (input_style, extra)])
            return lines
        suffix = [('class:ask.input.cursor', '█')] if is_focused and has_input else []
        if input_text and not is_focused:
            suffix = [('class:ask.option.selected', ' ✓')]
        return _wrapped_text_fragments(
            style=input_style,
            text=label,
            content_width=content_width,
            first_prefix=first_prefix,
            continuation_prefix=continuation_prefix,
            suffix=suffix,
        )

    if is_multi_select:
        marker = '☑' if is_selected else '☐'
        label_style = 'class:ask.option.focused' if is_focused else (
            'class:ask.option.selected' if is_selected else 'class:ask.option.label'
        )
        first_prefix = [
            (prefix_style, f'{pointer} '),
            ('class:ask.option.selected' if is_selected else 'class:ask.option.index', f'{marker} '),
        ]
    else:
        label_style = 'class:ask.option.focused' if is_focused else (
            'class:ask.option.selected' if is_selected else 'class:ask.option.label'
        )
        first_prefix = [
            (prefix_style, f'{pointer} '),
            ('class:ask.option.index', index_field),
        ]
    description_indent = _rendered_line_display_width(first_prefix)
    continuation_prefix = [(continuation_style, ' ' * description_indent)]
    suffix = [('class:ask.option.selected', ' ✓')] if (is_selected and not is_multi_select) else []
    lines = _wrapped_text_fragments(
        style=label_style,
        text=choice.label,
        content_width=content_width,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix,
        suffix=suffix,
    )
    if choice.description:
        description_style = 'class:ask.option.description.focused' if is_focused else (
            'class:ask.option.description.selected' if is_selected else 'class:ask.option.description'
        )
        description_prefix = [(description_style, ' ' * description_indent)]
        lines.extend(
            _wrapped_text_fragments(
                style=description_style,
                text=choice.description,
                content_width=content_width,
                first_prefix=description_prefix,
                continuation_prefix=description_prefix,
            )
        )
    return lines


def _note_display_lines(
    *,
    note_text: str,
    is_focused: bool,
    content_width: int,
) -> list[list[tuple[str, str]]]:
    label_style = 'class:ask.help'
    first_prefix = [(label_style, 'Notes: ')]
    continuation_prefix = [('class:ask', ' ' * _rendered_line_display_width(first_prefix))]
    trimmed = str(note_text or '')
    if is_focused and not trimmed:
        first_budget = max(1, content_width - _rendered_line_display_width(first_prefix) - 1)
        placeholder = 'Add details (optional)'
        placeholder_tail = placeholder[1:]
        wrapped_tail = _wrap_plain(placeholder_tail, first_budget) if placeholder_tail else ['']
        first_line: list[tuple[str, str]] = [*first_prefix, ('class:ask.input.cursor', '█')]
        if wrapped_tail and wrapped_tail[0]:
            first_line.append(('class:ask.input.placeholder', wrapped_tail[0]))
        lines: list[list[tuple[str, str]]] = [first_line]
        for extra in wrapped_tail[1:]:
            lines.append([*continuation_prefix, ('class:ask.input.placeholder', extra)])
        return lines
    suffix = [('class:ask.input.cursor', '█')] if is_focused else []
    return _wrapped_text_fragments(
        style='class:ask.input' if trimmed else 'class:ask.input.placeholder',
        text=trimmed or 'Add details (optional)',
        content_width=content_width,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix,
        suffix=suffix,
    )


def _make_preview_box(text: str, *, width: int, height: int) -> list[str]:
    inner_width = max(1, width - 2)
    wrapped: list[str] = []
    for paragraph in str(text or 'No preview available').splitlines() or ['No preview available']:
        wrapped.extend(_wrap_plain(paragraph, inner_width))
    visible = wrapped[: max(1, height - 2)]
    while len(visible) < max(1, height - 2):
        visible.append('')
    top = '╭' + ('─' * inner_width) + '╮'
    bottom = '╰' + ('─' * inner_width) + '╯'
    return [top, *[f'│{_pad_to_width(line, inner_width)}│' for line in visible], bottom]


def _fit_lines_preserving_suffix(
    *,
    prefix: list[list[tuple[str, str]]],
    body: list[list[tuple[str, str]]],
    suffix: list[list[tuple[str, str]]],
    max_lines: int,
) -> list[list[tuple[str, str]]]:
    max_lines = max(0, int(max_lines))
    if max_lines <= 0:
        return []
    if len(prefix) + len(body) + len(suffix) <= max_lines:
        return [*prefix, *body, *suffix]
    if len(prefix) + len(suffix) >= max_lines:
        suffix_budget = min(len(suffix), max_lines)
        prefix_budget = max(0, max_lines - suffix_budget)
        return [*prefix[:prefix_budget], *suffix[-suffix_budget:]]

    body_budget = max_lines - len(prefix) - len(suffix)
    if body_budget <= 0:
        clipped_body: list[list[tuple[str, str]]] = []
    elif body_budget == 1:
        clipped_body = [[('class:ask.warning', f'… {len(body)} review lines hidden')]]
    else:
        hidden = max(1, len(body) - (body_budget - 1))
        clipped_body = [
            *body[: body_budget - 1],
            [('class:ask.warning', f'… {hidden} review lines hidden')],
        ]
    return [*prefix, *clipped_body, *suffix]


def _macos_shift_pressed() -> bool:
    if sys.platform != 'darwin':
        return False
    try:
        application_services = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
        )
        application_services.CGEventSourceFlagsState.argtypes = [ctypes.c_uint32]
        application_services.CGEventSourceFlagsState.restype = ctypes.c_uint64
        flags = int(application_services.CGEventSourceFlagsState(1))
    except Exception:
        return False
    return bool(flags & _MACOS_SHIFT_MASK)


@contextmanager
def _shift_enter_compatibility_shim() -> Any:
    try:
        from prompt_toolkit.input import ansi_escape_sequences
        from prompt_toolkit.keys import Keys
    except ImportError:
        yield
        return

    previous: dict[str, Any] = {}
    applied_sequences: list[str] = []
    for sequence in _SHIFT_ENTER_SEQUENCES:
        previous[sequence] = ansi_escape_sequences.ANSI_SEQUENCES.get(sequence)
        ansi_escape_sequences.ANSI_SEQUENCES[sequence] = Keys.F24
        applied_sequences.append(sequence)
    try:
        yield
    finally:
        for sequence in applied_sequences:
            original = previous.get(sequence)
            if original is None:
                ansi_escape_sequences.ANSI_SEQUENCES.pop(sequence, None)
            else:
                ansi_escape_sequences.ANSI_SEQUENCES[sequence] = original


def _lines_to_formatted_text(
    lines: list[list[tuple[str, str]]],
    *,
    width: int,
) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    for line in lines:
        if line:
            fragments.extend(_truncate_fragments_to_width(line, width))
        fragments.append(('', '\n'))
    return fragments


def run_human_interaction_dialog(
    *,
    run_dir: Path,
    pending: dict[str, Any],
    ui_mode: str = 'developer',
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
    _input: Any | None = None,
    _output: Any | None = None,
) -> dict[str, dict[str, Any]] | object | None:
    del run_dir, ui_mode
    if not interaction_dialog_available():
        return None
    viewport = resolve_human_dialog_viewport()
    if not viewport.dialog_capable:
        return DIALOG_FALLBACK
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.document import Document
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.containers import DynamicContainer, HSplit, VSplit, Window
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension as D
        from prompt_toolkit.layout.processors import BeforeInput, ConditionalProcessor
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    questions = [item for item in pending.get('questions', []) if isinstance(item, dict)]
    if not questions:
        return None

    session = HumanQuestionSession(pending=pending)
    hide_submit_tab = _hide_submit_tab(questions)
    navigation_index = 0
    focus_region: FocusRegion = 'options'
    submit_cursor = 0
    editor_focus: _EditorFocusState | None = None
    pending_focus_buffer: Buffer | None = None
    cursor_by_question_id: dict[str, int] = {}
    app: Any | None = None
    list_focus_control: Any | None = None

    def _make_buffer(*, text: str, name: str) -> Buffer:
        return Buffer(document=Document(text, len(text)), multiline=True, name=name)

    input_buffers_by_question_id = {
        str(question.get('question_id', '') or '').strip(): _make_buffer(
            text=str(session.draft_for(str(question.get('question_id', '') or '').strip()).free_text or ''),
            name=f'human-question-input:{str(question.get("question_id", "") or "").strip()}',
        )
        for question in questions
        if str(question.get('question_id', '') or '').strip()
    }
    note_buffers_by_target: dict[tuple[str, str], Buffer] = {}
    for question in questions:
        question_id = str(question.get('question_id', '') or '').strip()
        if not question_id:
            continue
        draft = session.draft_for(question_id)
        if len(draft.selected_labels) == 1 and str(draft.free_text or '').strip():
            option_label = draft.selected_labels[0]
            note_buffers_by_target[(question_id, option_label)] = _make_buffer(
                text=str(draft.free_text or ''),
                name=f'human-question-note:{question_id}:{option_label}',
            )

    def _max_navigation_index() -> int:
        return len(questions) - 1 if hide_submit_tab else len(questions)

    def _current_question() -> dict[str, Any] | None:
        if navigation_index >= len(questions):
            return None
        return questions[navigation_index]

    def _question_by_id(question_id: str) -> dict[str, Any] | None:
        normalized = str(question_id or '').strip()
        for question in questions:
            if str(question.get('question_id', '') or '').strip() == normalized:
                return question
        return None

    def _current_question_id() -> str:
        question = _current_question()
        if question is None:
            return ''
        return str(question.get('question_id', '') or '').strip()

    def _choices_for_question(
        question: dict[str, Any] | None,
        *,
        question_index: int,
        include_submit_row: bool = True,
    ) -> list[_DialogChoice]:
        if question is None:
            return []
        choices = _dialog_choices(question)
        if include_submit_row and bool(question.get('multi_select', False)):
            choices.append(
                _DialogChoice(
                    kind='option',
                    label=_submit_row_label(current_question_index=question_index, questions=questions),
                    value='__submit_row__',
                )
            )
        return choices

    def _current_choices(*, include_submit_row: bool = True) -> list[_DialogChoice]:
        question = _current_question()
        return _choices_for_question(
            question,
            question_index=navigation_index if question is not None else len(questions),
            include_submit_row=include_submit_row,
        )

    def _current_cursor() -> int:
        question = _current_question()
        if question is None:
            return submit_cursor
        question_id = str(question.get('question_id', '') or '').strip()
        if question_id not in cursor_by_question_id:
            cursor_by_question_id[question_id] = _default_cursor_index(question, session)
        option_count = len(_current_choices())
        cursor_by_question_id[question_id] = max(0, min(cursor_by_question_id[question_id], max(0, option_count - 1)))
        return cursor_by_question_id[question_id]

    def _set_current_cursor(index: int) -> None:
        question_id = _current_question_id()
        option_count = len(_current_choices())
        cursor_by_question_id[question_id] = max(0, min(index, max(0, option_count - 1)))

    def _selected_choice() -> _DialogChoice | None:
        choices = _current_choices()
        if not choices:
            return None
        return choices[_current_cursor()]

    def _current_option_note_target() -> tuple[str, str] | None:
        question = _current_question()
        choice = _selected_choice()
        if focus_region != 'options':
            return None
        if not _question_supports_option_notes(question):
            return None
        if question is None or choice is None or choice.kind != 'option' or choice.value == '__submit_row__':
            return None
        question_id = str(question.get('question_id', '') or '').strip()
        option_label = str(choice.value or '').strip()
        if not question_id or not option_label:
            return None
        return question_id, option_label

    def _question_supports_notes_here() -> bool:
        return _current_option_note_target() is not None

    def _input_buffer(question_id: str) -> Buffer:
        normalized = str(question_id or '').strip()
        if normalized not in input_buffers_by_question_id:
            input_buffers_by_question_id[normalized] = _make_buffer(
                text='',
                name=f'human-question-input:{normalized}',
            )
        return input_buffers_by_question_id[normalized]

    def _clear_other_input(question_id: str) -> None:
        normalized = str(question_id or '').strip()
        _clear_custom_input_after_option_change(
            session=session,
            question_id=normalized,
            input_buffer=input_buffers_by_question_id.get(normalized),
        )

    def _note_buffer(question_id: str, option_label: str) -> Buffer:
        key = (str(question_id or '').strip(), str(option_label or '').strip())
        if key not in note_buffers_by_target:
            note_buffers_by_target[key] = _make_buffer(
                text='',
                name=f'human-question-note:{key[0]}:{key[1]}',
            )
        return note_buffers_by_target[key]

    def _active_editor_buffer() -> Buffer | None:
        if editor_focus is None:
            return None
        if editor_focus.kind == 'other_input':
            return _input_buffer(editor_focus.question_id)
        if not editor_focus.option_label:
            return None
        return _note_buffer(editor_focus.question_id, editor_focus.option_label)

    def _has_editor_focus() -> bool:
        return editor_focus is not None

    def _is_note_editor_active() -> bool:
        return editor_focus is not None and editor_focus.kind == 'note_input'

    def _is_input_editor_active() -> bool:
        return editor_focus is not None and editor_focus.kind == 'other_input'

    def _sync_input_buffer(question_id: str) -> None:
        question = _question_by_id(question_id)
        if question is None:
            return
        text = _input_buffer(question_id).text.strip() or None
        if not bool(question.get('multi_select', False)):
            session.update_single_select(question_id, None)
        session.update_free_text(question_id, text)

    def _sync_option_note_buffer(*, question_id: str, option_label: str) -> None:
        buffer = _note_buffer(question_id, option_label)
        normalized = str(buffer.text or '')
        trimmed = normalized.strip()
        if trimmed:
            note_buffers_by_target[(question_id, option_label)] = buffer
        else:
            note_buffers_by_target.pop((question_id, option_label), None)
        draft = session.draft_for(question_id)
        if draft.selected_labels == [option_label]:
            session.update_free_text(question_id, trimmed or None)

    def _blur_editor() -> None:
        nonlocal editor_focus, pending_focus_buffer
        if editor_focus is None:
            return
        if editor_focus.kind == 'other_input':
            _sync_input_buffer(editor_focus.question_id)
        elif editor_focus.option_label:
            _sync_option_note_buffer(question_id=editor_focus.question_id, option_label=editor_focus.option_label)
        editor_focus = None
        pending_focus_buffer = None
        if app is not None:
            if list_focus_control is not None:
                try:
                    app.layout.focus(list_focus_control)
                except ValueError:
                    pass
            app.invalidate()

    def _focus_pending_buffer_soon() -> None:
        if app is None or pending_focus_buffer is None:
            return

        def _apply_focus() -> None:
            nonlocal pending_focus_buffer
            if pending_focus_buffer is None:
                return
            try:
                app.layout.focus(pending_focus_buffer)
            except ValueError:
                try:
                    app.layout.focus(app.layout.container)
                except ValueError:
                    app.invalidate()
                    return
                current_buffer = getattr(getattr(app.layout.current_control, 'buffer', None), 'name', None)
                if current_buffer != pending_focus_buffer.name:
                    app.invalidate()
                    return
            pending_focus_buffer = None
            app.invalidate()

        loop = getattr(app, 'loop', None)
        if loop is not None:
            loop.call_soon(_apply_focus)
        else:
            _apply_focus()

    def _set_editor_focus(state: _EditorFocusState | None) -> None:
        nonlocal editor_focus, pending_focus_buffer
        if editor_focus == state:
            return
        if editor_focus is not None and editor_focus != state:
            if editor_focus.kind == 'other_input':
                _sync_input_buffer(editor_focus.question_id)
            elif editor_focus.option_label:
                _sync_option_note_buffer(question_id=editor_focus.question_id, option_label=editor_focus.option_label)
        editor_focus = state
        if app is not None:
            target_buffer = _active_editor_buffer()
            pending_focus_buffer = target_buffer
            app.invalidate()
            _focus_pending_buffer_soon()

    def _maybe_focus_input_editor() -> None:
        choice = _selected_choice()
        question_id = _current_question_id()
        if focus_region != 'options' or choice is None or choice.kind != 'input' or not question_id:
            if editor_focus is not None and editor_focus.kind == 'other_input':
                _blur_editor()
            return
        _set_editor_focus(_EditorFocusState(kind='other_input', question_id=question_id))

    def _set_navigation(index: int, *, validate_current: bool = False) -> bool:
        nonlocal navigation_index, focus_region, editor_focus
        if validate_current and navigation_index < len(questions):
            question_id = _current_question_id()
            error = session.validate_question(question_id)
            if error:
                session.state.validation_errors[question_id] = error
                return False
        if editor_focus is not None:
            _blur_editor()
        navigation_index = max(0, min(index, _max_navigation_index()))
        focus_region = 'submit_actions' if navigation_index >= len(questions) else 'options'
        editor_focus = None
        if navigation_index < len(questions):
            session.leave_review()
            session.set_current_question_index(navigation_index)
            _maybe_focus_input_editor()
        else:
            session.enter_review()
        return True

    def _finish_or_advance_after_single_choice() -> None:
        if hide_submit_tab and len(questions) == 1:
            _submit_answers()
            return
        if navigation_index < len(questions) - 1:
            _set_navigation(navigation_index + 1)
            return
        _set_navigation(len(questions))

    def _submit_answers() -> None:
        errors = session.validate_all()
        if errors:
            return
        if editor_focus is not None:
            _blur_editor()
        app.exit(result=normalize_human_answer_mapping(session.canonical_answers(), pending=pending))

    def _commit_editor() -> None:
        nonlocal editor_focus
        if editor_focus is None:
            return
        if editor_focus.kind == 'other_input':
            question_id = editor_focus.question_id
            question = _question_by_id(question_id)
            _sync_input_buffer(question_id)
            if not human_answer_has_value(session.answer_payload_for(question_id)):
                error = session.validate_question(question_id)
                if error:
                    session.state.validation_errors[question_id] = error
                if app is not None:
                    app.invalidate()
                return
            editor_focus = None
            if question is not None and bool(question.get('multi_select', False)):
                if app is not None:
                    app.invalidate()
                return
            _finish_or_advance_after_single_choice()
            if app is not None:
                app.invalidate()
            return
        question_id = editor_focus.question_id
        option_label = str(editor_focus.option_label or '').strip()
        if not option_label:
            return
        session.update_single_select(question_id, option_label)
        session.update_free_text(question_id, _note_buffer(question_id, option_label).text.strip() or None)
        editor_focus = None
        _finish_or_advance_after_single_choice()
        if app is not None:
            app.invalidate()

    def _render_nav_lines() -> list[list[tuple[str, str]]]:
        return [
            _navigation_bar_fragments(
                questions=questions,
                session=session,
                current_index=navigation_index,
                width=viewport.columns,
                hide_submit_tab=hide_submit_tab,
            )
        ]

    def _note_text_for_target(target: tuple[str, str] | None) -> str:
        if target is None:
            return ''
        buffer = note_buffers_by_target.get(target)
        return '' if buffer is None else str(buffer.text or '')

    def _note_visible_at_bottom() -> bool:
        if _is_note_editor_active():
            return True
        return bool(_note_text_for_target(_current_option_note_target()).strip())

    def _current_editor_mode() -> str | None:
        if editor_focus is None:
            return None
        return 'note' if editor_focus.kind == 'note_input' else 'input'

    def _render_footer_lines(question: dict[str, Any], *, editor_mode: str | None = None) -> list[list[tuple[str, str]]]:
        lines: list[list[tuple[str, str]]] = [_divider_line(viewport.columns)]
        footer_number = len(_dialog_choices(question)) + 1
        pointer = '❯' if focus_region == 'footer' else ' '
        pointer_style = 'class:ask.pointer' if focus_region == 'footer' else 'class:ask'
        lines.extend(
            _wrapped_text_fragments(
                style='class:ask.footer',
                text=_FOOTER_CHAT_LABEL,
                content_width=viewport.columns,
                first_prefix=[
                    (pointer_style, f'{pointer} '),
                    ('class:ask.option.index', f'{footer_number}. '),
                ],
            )
        )
        lines.append([])
        for help_line in _human_help_text(
            columns=viewport.columns,
            question_count=len(questions),
            is_multi_select=bool(question.get('multi_select', False)),
            notes_available=(
                _question_supports_option_notes(question)
                and (
                    _is_note_editor_active()
                    or _note_visible_at_bottom()
                    or bool(
                        question.get('notes_available')
                        or question.get('allow_notes')
                        or question.get('notes_enabled')
                        or question.get('option_notes')
                    )
                )
            ),
            editor_mode=editor_mode,
        ):
            lines.append([('class:ask.help', help_line)])
        return lines

    def _render_choice_blocks(
        *,
        question: dict[str, Any],
        choices: list[_DialogChoice],
        content_width: int,
    ) -> tuple[list[list[list[tuple[str, str]]]], int]:
        question_id = str(question.get('question_id', '') or '').strip()
        draft = session.draft_for(question_id)
        cursor = _current_cursor()
        is_multi = bool(question.get('multi_select', False))
        numbered_count = len([choice for choice in choices if choice.value != '__submit_row__'])
        max_index_width = max(2, _display_width(f'{max(1, numbered_count)}.'))
        blocks: list[list[list[tuple[str, str]]]] = []
        for index, choice in enumerate(choices):
            is_submit_row = choice.value == '__submit_row__'
            if is_submit_row:
                pointer = '❯' if focus_region == 'options' and cursor == index else ' '
                style = 'class:ask.pointer' if pointer == '❯' else 'class:ask'
                label_style = 'class:ask.option.focused' if pointer == '❯' else 'class:ask.option.label'
                blocks.append(
                    [[
                        (style, f'{pointer} '),
                        ('class:ask.option.index', ' ' * (max_index_width + 1)),
                        (label_style, choice.label),
                    ]]
                )
                continue
            selected = choice.value in draft.selected_labels if choice.kind == 'option' else bool(_input_buffer(question_id).text.strip())
            blocks.append(
                _choice_display_lines(
                    choice=choice,
                    number=index + 1,
                    is_focused=focus_region == 'options' and cursor == index and not _has_editor_focus(),
                    is_selected=selected,
                    is_multi_select=is_multi,
                    input_text=_input_buffer(question_id).text,
                    max_index_width=max_index_width,
                    content_width=content_width,
                )
            )
        return blocks, max_index_width

    def _bottom_note_static_lines(*, content_width: int) -> list[list[tuple[str, str]]] | None:
        if _is_note_editor_active():
            return None
        target = _current_option_note_target()
        text = _note_text_for_target(target)
        if not text.strip():
            return None
        first_prefix = [('class:ask.pointer', '❯ ')]
        continuation_prefix = [('class:ask', ' ' * _rendered_line_display_width(first_prefix))]
        return _wrapped_text_fragments(
            style='class:ask.input',
            text=text,
            content_width=content_width,
            first_prefix=first_prefix,
            continuation_prefix=continuation_prefix,
        )

    def _editor_prefix_fragments(*, content_width: int, max_index_width: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]], str]:
        if editor_focus is None:
            return [], [], ''
        if editor_focus.kind == 'other_input':
            choice_index = _current_cursor()
            first_prefix = [
                ('class:ask.pointer', '❯ '),
                ('class:ask.option.index', _pad_to_width(f'{choice_index + 1}.', max_index_width) + ' '),
            ]
            continuation_prefix = [('class:ask', ' ' * _rendered_line_display_width(first_prefix))]
            return first_prefix, continuation_prefix, _input_placeholder(_question_by_id(editor_focus.question_id) or {})
        first_prefix = [('class:ask.pointer', '❯ ')]
        continuation_prefix = [('class:ask', ' ' * _rendered_line_display_width(first_prefix))]
        return first_prefix, continuation_prefix, 'Add notes'

    def _editor_estimated_height(*, content_width: int, max_index_width: int) -> int:
        buffer = _active_editor_buffer()
        if buffer is None:
            return 0
        first_prefix, continuation_prefix, placeholder = _editor_prefix_fragments(
            content_width=content_width,
            max_index_width=max_index_width,
        )
        prefix_width = _rendered_line_display_width(first_prefix)
        continuation_width = _rendered_line_display_width(continuation_prefix)
        text = buffer.text or placeholder
        wrapped = _wrap_plain_with_widths(
            text,
            first_width=max(1, content_width - prefix_width),
            continuation_width=max(1, content_width - continuation_width),
        )
        return max(1, len(wrapped))

    def _build_editor_window(*, content_width: int, max_index_width: int) -> Window:
        buffer = _active_editor_buffer()
        if buffer is None:
            return Window(height=0)
        first_prefix, continuation_prefix, placeholder = _editor_prefix_fragments(
            content_width=content_width,
            max_index_width=max_index_width,
        )
        key_bindings = KeyBindings()

        @key_bindings.add('enter', eager=True)
        def _(event: Any) -> None:
            if _macos_shift_pressed():
                event.current_buffer.insert_text('\n')
                return
            _commit_editor()

        @key_bindings.add(_SHIFT_ENTER_INTERNAL_KEY, eager=True)
        def _(event: Any) -> None:
            event.current_buffer.insert_text('\n')

        @key_bindings.add('escape', eager=True)
        def _(event: Any) -> None:
            del event
            _blur_editor()

        @key_bindings.add('tab', eager=True)
        @key_bindings.add('s-tab', eager=True)
        def _(event: Any) -> None:
            if editor_focus is not None and editor_focus.kind == 'note_input':
                del event
                _blur_editor()
                return
            event.current_buffer.insert_text('\t')

        def _editor_boundary_navigation(direction: str) -> bool:
            nonlocal focus_region
            if editor_focus is None or editor_focus.kind != 'other_input':
                return False
            document = buffer.document
            if direction == 'up':
                if document.cursor_position_row > 0:
                    return False
                target_index = max(0, _current_cursor() - 1)
                _blur_editor()
                focus_region = 'options'
                _set_current_cursor(target_index)
                if app is not None:
                    app.invalidate()
                return True
            if document.cursor_position_row < document.line_count - 1:
                return False
            transition = _next_focus_from_down(
                region='options',
                index=_current_cursor(),
                option_count=len(_current_choices()),
            )
            _blur_editor()
            focus_region = transition.region
            if focus_region == 'options':
                _set_current_cursor(transition.index)
            if app is not None:
                app.invalidate()
            return True

        @key_bindings.add('up', eager=True)
        def _(event: Any) -> None:
            if _editor_boundary_navigation('up'):
                return
            event.current_buffer.cursor_up()

        @key_bindings.add('down', eager=True)
        def _(event: Any) -> None:
            if _editor_boundary_navigation('down'):
                return
            event.current_buffer.cursor_down()

        def _empty_placeholder_fragments() -> list[tuple[str, str]]:
            if editor_focus is not None and editor_focus.kind == 'other_input':
                tail = placeholder[1:] if placeholder else ''
                return [('class:ask.input.cursor', '█'), ('class:ask.input.placeholder', tail)]
            if editor_focus is not None and editor_focus.kind == 'note_input':
                return [('class:ask.input.cursor', '│'), ('class:ask.input.placeholder', placeholder)]
            return [('class:ask.input.cursor', '█'), ('class:ask.input.placeholder', placeholder)]

        control = BufferControl(
            buffer=buffer,
            focusable=True,
            key_bindings=key_bindings,
            input_processors=[
                ConditionalProcessor(
                    BeforeInput(_empty_placeholder_fragments),
                    Condition(lambda: not buffer.text),
                )
            ],
        )
        return Window(
            content=control,
            wrap_lines=True,
            dont_extend_height=True,
            height=D.exact(
                _editor_estimated_height(
                    content_width=content_width,
                    max_index_width=max_index_width,
                )
            ),
            style='class:ask.input',
            get_line_prefix=lambda line_number, wrap_count: first_prefix if line_number == 0 and wrap_count == 0 else continuation_prefix,
        )

    def _question_top_lines(question: dict[str, Any]) -> list[list[tuple[str, str]]]:
        lines: list[list[tuple[str, str]]] = [_divider_line(viewport.columns), *_render_nav_lines(), []]
        for line in _wrap_plain(str(question.get('question', '') or ''), viewport.columns):
            lines.append([('class:ask.title', line)])
        lines.append([])
        return lines

    def _question_bottom_lines(question: dict[str, Any], *, editor_mode: str | None = None) -> list[list[tuple[str, str]]]:
        question_id = str(question.get('question_id', '') or '').strip()
        lines: list[list[tuple[str, str]]] = []
        note_lines = _bottom_note_static_lines(content_width=viewport.columns)
        if note_lines is not None:
            lines.extend(note_lines)
            lines.append([])
        current_error = session.question_error(question_id)
        if current_error:
            for line in _wrap_plain(current_error, viewport.columns):
                lines.append([('class:ask.error', line)])
            lines.append([])
        lines.append([])
        lines.extend(_render_footer_lines(question, editor_mode=editor_mode))
        return lines

    def _question_plain_segments(question: dict[str, Any], *, question_index: int) -> tuple[list[list[tuple[str, str]]], int, list[list[tuple[str, str]]], list[list[tuple[str, str]]]]:
        choices = _choices_for_question(question, question_index=question_index)
        top_lines = _question_top_lines(question)
        bottom_lines = _question_bottom_lines(question, editor_mode=_current_editor_mode())
        blocks, max_index_width = _render_choice_blocks(question=question, choices=choices, content_width=viewport.columns)
        active_index = _current_cursor()
        lines_before = list(top_lines)
        lines_after: list[list[tuple[str, str]]] = []
        input_editor_split = False

        for index, block in enumerate(blocks):
            if _is_input_editor_active() and index == active_index:
                input_editor_split = True
                lines_after.extend([line for later_block in blocks[index + 1 :] for line in later_block])
                break
            lines_before.extend(block)
        else:
            lines_after = []

        if input_editor_split:
            lines_after.extend(bottom_lines)
            return lines_before, max_index_width, lines_after, []
        if _is_note_editor_active():
            lines_before.append([])
            lines_after.extend(bottom_lines)
            return lines_before, max_index_width, lines_after, []
        if lines_after:
            lines_after.extend(bottom_lines)
        else:
            lines_before.extend(bottom_lines)
        return lines_before, max_index_width, lines_after, []

    def _question_preview_segments(question: dict[str, Any], *, question_index: int) -> tuple[list[list[tuple[str, str]]], int, list[list[tuple[str, str]]], list[str], int]:
        left_width = 30
        choices = _choices_for_question(question, question_index=question_index)
        blocks, max_index_width = _render_choice_blocks(question=question, choices=choices, content_width=left_width)
        left_before: list[list[tuple[str, str]]] = []
        left_after: list[list[tuple[str, str]]] = []
        for block in blocks:
            left_before.extend(block)
        cursor_choice = choices[_current_cursor()] if choices else None
        preview = cursor_choice.preview if cursor_choice and cursor_choice.kind == 'option' else None
        estimated_left_height = len(left_before) + len(left_after)
        preview_lines = _make_preview_box(
            preview or 'No preview available',
            width=max(1, viewport.columns - left_width - 4),
            height=max(3, estimated_left_height or 3),
        )
        return left_before, max_index_width, left_after, preview_lines, left_width

    def _build_submit_lines() -> list[list[tuple[str, str]]]:
        errors = session.validate_all()
        header_lines: list[list[tuple[str, str]]] = [_divider_line(viewport.columns), *_render_nav_lines(), []]
        for line in _wrap_plain('Review your answers', viewport.columns):
            header_lines.append([('class:ask.title', line)])
        header_lines.append([])
        if errors:
            for line in _wrap_plain('Some required questions still need an answer.', viewport.columns):
                header_lines.append([('class:ask.warning', line)])
            header_lines.append([])
        body_lines: list[list[tuple[str, str]]] = []
        for index, question in enumerate(questions, start=1):
            question_id = str(question.get('question_id', '') or '').strip()
            header = str(question.get('header', '') or '').strip() or question_id or f'Q{index}'
            answer = human_answer_text(session.answer_payload_for(question_id)) or '<empty>'
            body_lines.extend(
                _wrapped_text_fragments(
                    style='class:ask.option.label',
                    text=header,
                    content_width=viewport.columns,
                    first_prefix=[('class:ask.option.label', '• ')],
                    continuation_prefix=[('class:ask', '  ')],
                )
            )
            body_lines.extend(
                _wrapped_text_fragments(
                    style='class:ask.review.answer',
                    text=answer,
                    content_width=viewport.columns,
                    first_prefix=[('class:ask.review.answer', '  → ')],
                    continuation_prefix=[('class:ask', '    ')],
                )
            )
        suffix_lines: list[list[tuple[str, str]]] = [[]]
        actions = ['Submit answers', 'Cancel']
        for index, action in enumerate(actions):
            pointer = '❯' if focus_region == 'submit_actions' and submit_cursor == index else ' '
            style = 'class:ask.pointer' if pointer == '❯' else 'class:ask'
            suffix_lines.extend(
                _wrapped_text_fragments(
                    style='class:ask.option.label',
                    text=action,
                    content_width=viewport.columns,
                    first_prefix=[(style, f'{pointer} '), ('class:ask.option.index', f'{index + 1}. ')],
                    continuation_prefix=[('class:ask', ' ' * 5)],
                )
            )
        suffix_lines.append([])
        for help_line in _human_help_text(
            columns=viewport.columns,
            question_count=len(questions),
            is_multi_select=False,
            on_submit_screen=True,
        ):
            suffix_lines.append([('class:ask.help', help_line)])
        return _fit_lines_preserving_suffix(
            prefix=header_lines,
            body=body_lines,
            suffix=suffix_lines,
            max_lines=viewport.lines,
        )

    def _static_window(lines: list[list[tuple[str, str]]], *, width: int | None = None) -> Window:
        window_width = viewport.columns if width is None else width
        return Window(
            FormattedTextControl(_lines_to_formatted_text(lines, width=window_width)),
            wrap_lines=False,
            always_hide_cursor=True,
            width=window_width,
            height=len(lines),
            dont_extend_height=True,
        )

    def _list_focus_anchor() -> Window:
        return Window(
            content=list_focus_control,
            height=0,
            dont_extend_height=True,
            always_hide_cursor=True,
        )

    def _submit_container() -> Window:
        return _static_window(_build_submit_lines())

    def _question_container(question: dict[str, Any], *, question_index: int, show_preview: bool):
        if not show_preview:
            before_lines, max_index_width, after_lines, _ = _question_plain_segments(question, question_index=question_index)
            children: list[Any] = [_static_window(before_lines)]
            if _has_editor_focus():
                children.append(_build_editor_window(content_width=viewport.columns, max_index_width=max_index_width))
            if after_lines:
                children.append(_static_window(after_lines))
            return HSplit(children)

        top_lines = _question_top_lines(question)
        bottom_lines = _question_bottom_lines(question, editor_mode=_current_editor_mode())
        left_before, max_index_width, left_after, preview_lines, left_width = _question_preview_segments(question, question_index=question_index)
        right_width = max(1, viewport.columns - left_width - 4)
        left_children: list[Any] = []
        if left_before:
            left_children.append(_static_window(left_before, width=left_width))
        if _has_editor_focus() and not _is_note_editor_active():
            left_children.append(_build_editor_window(content_width=left_width, max_index_width=max_index_width))
        if left_after:
            left_children.append(_static_window(left_after, width=left_width))
        if not left_children:
            left_children.append(_static_window([], width=left_width))
        body = VSplit(
            [
                HSplit(left_children),
                _static_window([[('class:ask.preview.border' if index in {0, len(preview_lines) - 1} else 'class:ask.preview.text', line)] for index, line in enumerate(preview_lines)], width=right_width),
            ],
            padding=4,
        )
        children: list[Any] = [
            _static_window(top_lines),
            body,
        ]
        if _is_note_editor_active():
            children.append(_static_window([[]]))
            children.append(_build_editor_window(content_width=viewport.columns, max_index_width=max_index_width))
        children.append(_static_window(bottom_lines))
        return HSplit(children)

    preview_visible_by_question_id: dict[str, bool] = {}
    for index, question in enumerate(questions):
        question_id = str(question.get('question_id', '') or '').strip()
        plain_before, _, plain_after, _ = _question_plain_segments(question, question_index=index)
        plain_lines = [*plain_before, *plain_after]
        if len(plain_lines) > viewport.lines:
            return DIALOG_FALLBACK
        preview_visible_by_question_id[question_id] = False
        if _should_use_preview_layout(question) and viewport.preview_eligible:
            top_lines = _question_top_lines(question)
            bottom_lines = _question_bottom_lines(question)
            left_before, _, left_after, preview_lines, _ = _question_preview_segments(question, question_index=index)
            preview_line_count = len(top_lines) + max(len(left_before) + len(left_after), len(preview_lines)) + len(bottom_lines)
            if preview_line_count <= viewport.lines:
                preview_visible_by_question_id[question_id] = True
    if not hide_submit_tab:
        saved_validation_errors = dict(session.state.validation_errors)
        if len(_build_submit_lines()) > viewport.lines:
            session.state.validation_errors = saved_validation_errors
            return DIALOG_FALLBACK
        session.state.validation_errors = saved_validation_errors

    def _root_container():
        question = _current_question()
        body = _submit_container() if question is None else _question_container(
            question,
            question_index=navigation_index,
            show_preview=preview_visible_by_question_id.get(str(question.get('question_id', '') or '').strip(), False),
        )
        return HSplit([_list_focus_anchor(), body])

    list_focus_control = FormattedTextControl('', focusable=Condition(lambda: not _has_editor_focus()))

    key_bindings = KeyBindings()
    list_mode = Condition(lambda: not _has_editor_focus())

    @key_bindings.add('escape', filter=list_mode)
    def _(event: Any) -> None:
        event.app.exit(result=None)

    @key_bindings.add('left', filter=list_mode)
    def _(event: Any) -> None:
        del event
        _set_navigation(navigation_index - 1)
        if app is not None:
            app.invalidate()

    @key_bindings.add('right', filter=list_mode)
    def _(event: Any) -> None:
        del event
        _set_navigation(navigation_index + 1, validate_current=navigation_index < len(questions))
        if app is not None:
            app.invalidate()

    @key_bindings.add('tab', filter=list_mode)
    @key_bindings.add('s-tab', filter=list_mode)
    def _(event: Any) -> None:
        del event
        target = _current_option_note_target()
        if target is None:
            return
        _set_editor_focus(_EditorFocusState(kind='note_input', question_id=target[0], option_label=target[1]))

    @key_bindings.add('up', filter=list_mode)
    @key_bindings.add('k', filter=list_mode)
    @key_bindings.add('c-p', filter=list_mode)
    def _(event: Any) -> None:
        nonlocal focus_region, submit_cursor
        del event
        if navigation_index >= len(questions):
            transition = _previous_focus_from_up(region='submit_actions', index=submit_cursor, option_count=2)
            submit_cursor = transition.index
        else:
            transition = _previous_focus_from_up(region=focus_region, index=_current_cursor(), option_count=len(_current_choices()))
            focus_region = transition.region
            if focus_region == 'options':
                _set_current_cursor(transition.index)
                _maybe_focus_input_editor()
        if app is not None:
            app.invalidate()

    @key_bindings.add('down', filter=list_mode)
    @key_bindings.add('j', filter=list_mode)
    @key_bindings.add('c-n', filter=list_mode)
    def _(event: Any) -> None:
        nonlocal focus_region, submit_cursor
        del event
        if navigation_index >= len(questions):
            transition = _next_focus_from_down(region='submit_actions', index=submit_cursor, option_count=2)
            submit_cursor = transition.index
        else:
            transition = _next_focus_from_down(region=focus_region, index=_current_cursor(), option_count=len(_current_choices()))
            focus_region = transition.region
            if focus_region == 'options':
                _set_current_cursor(transition.index)
                _maybe_focus_input_editor()
        if app is not None:
            app.invalidate()

    @key_bindings.add('enter', filter=list_mode)
    def _(event: Any) -> None:
        del event
        if navigation_index >= len(questions):
            if submit_cursor == 0:
                _submit_answers()
            else:
                app.exit(result=None)
            if app is not None:
                app.invalidate()
            return
        question = _current_question()
        question_id = _current_question_id()
        if question is None or not question_id:
            return
        if focus_region == 'footer':
            app.exit(result=HumanDialogFallbackResult(draft_answers_by_question_id=session.canonical_answers()))
            return
        choice = _selected_choice()
        if choice is None:
            return
        if bool(question.get('multi_select', False)):
            if choice.value == '__submit_row__':
                if _submit_row_label(current_question_index=navigation_index, questions=questions) == 'Next':
                    _set_navigation(navigation_index + 1, validate_current=True)
                else:
                    _set_navigation(len(questions), validate_current=True)
            if app is not None:
                app.invalidate()
            return
        if choice.kind == 'input':
            _maybe_focus_input_editor()
            return
        session.update_single_select(question_id, choice.value)
        target = _current_option_note_target()
        if target is not None:
            session.update_free_text(question_id, _note_buffer(target[0], target[1]).text.strip() or None)
        else:
            _clear_other_input(question_id)
        _finish_or_advance_after_single_choice()
        if app is not None:
            app.invalidate()

    @key_bindings.add('<any>', filter=list_mode)
    def _(event: Any) -> None:
        nonlocal pending_focus_buffer
        data = str(event.data or '')
        if not data or not data.isprintable() or navigation_index >= len(questions):
            return
        question = _current_question()
        question_id = _current_question_id()
        choice = _selected_choice()
        if question is None or choice is None:
            return
        if data == ' ' and bool(question.get('multi_select', False)) and choice.kind == 'option' and choice.value != '__submit_row__':
            draft = session.draft_for(question_id)
            selected = list(draft.selected_labels)
            if choice.value in selected:
                selected = [item for item in selected if item != choice.value]
            else:
                selected.append(choice.value)
            session.update_multi_select(question_id, selected)
            _clear_other_input(question_id)
            if app is not None:
                app.invalidate()
            return
        if choice.kind == 'input':
            _maybe_focus_input_editor()
            buffer = _input_buffer(question_id)
            if data:
                buffer.insert_text(data)
            if app is not None:
                pending_focus_buffer = buffer
                app.invalidate()

    app = Application(
        layout=Layout(DynamicContainer(_root_container)),
        key_bindings=key_bindings,
        style=Style.from_dict(
            _dialog_style_rules(
                theme_setting=theme_setting,
                use_terminal_background=use_terminal_background,
            )
        ),
        full_screen=False,
        input=_input,
        output=_output,
    )
    app.ttimeoutlen = _ESCAPE_FLUSH_TIMEOUT_SECONDS
    app.timeoutlen = _KEY_SEQUENCE_TIMEOUT_SECONDS

    def _apply_pending_focus(_sender: Any) -> None:
        nonlocal pending_focus_buffer
        if pending_focus_buffer is None:
            return
        try:
            app.layout.focus(pending_focus_buffer)
        except ValueError:
            return
        pending_focus_buffer = None

    app.after_render += _apply_pending_focus
    _maybe_focus_input_editor()
    with _shift_enter_compatibility_shim():
        return app.run()
