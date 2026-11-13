from __future__ import annotations

from dataclasses import dataclass
import sys
from collections.abc import Iterable
from typing import Any, Callable

from ownpi.chat.theme import (
    build_prompt_toolkit_picker_style,
    get_ui_tokens,
    resolve_theme_setting,
)
from ownpi.chat.command_registry import slash_command_summaries


PICKER_MIN_WIDTH = 28
PICKER_MAX_WIDTH = 68
PICKER_OUTER_PADDING = 2
PICKER_LABEL_INDENT = 2
PICKER_META_INDENT = 5
PICKER_MAX_VISIBLE_CANDIDATES = 8
ESCAPE_FLUSH_TIMEOUT_SECONDS = 0.05
KEY_SEQUENCE_TIMEOUT_SECONDS = 0.05


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    argument_hint: str | None = None
    trust_level: str = ''
    source: str = ''


@dataclass(frozen=True)
class PickerCandidate:
    label: str
    insert_text: str
    meta: str
    kind: str


DEFAULT_SLASH_COMMANDS: tuple[SlashCommand, ...] = tuple(
    SlashCommand(name=name, description=description)
    for name, description in slash_command_summaries()
)


CommandProvider = Callable[[], tuple[SlashCommand, ...]]


def slash_commands_from_summaries(summaries: Iterable[tuple[Any, ...]]) -> tuple[SlashCommand, ...]:
    commands: list[SlashCommand] = []
    for item in summaries:
        if len(item) == 2:
            name, description = item
            commands.append(SlashCommand(name=name, description=description))
            continue
        name, description, argument_hint, trust_level, source = (*item, '', '', '')[:5]
        commands.append(
            SlashCommand(
                name=str(name),
                description=str(description),
                argument_hint=str(argument_hint).strip() or None,
                trust_level=str(trust_level or ''),
                source=str(source or ''),
            )
        )
    return tuple(commands)


def match_slash_commands(buffer: str, commands: tuple[SlashCommand, ...] = DEFAULT_SLASH_COMMANDS) -> list[SlashCommand]:
    if not buffer.startswith("/"):
        return []
    if any(ch.isspace() for ch in buffer):
        return []
    return [command for command in commands if command.name.startswith(buffer)]


def match_skill_mentions(buffer: str, skill_names: list[str]) -> list[str]:
    if not buffer.startswith("$"):
        return []
    if any(ch.isspace() for ch in buffer):
        return []
    prefix = buffer[1:].casefold()
    if not prefix:
        return list(skill_names)
    return [name for name in skill_names if name.casefold().startswith(prefix)]


def build_picker_candidates(
    buffer: str,
    commands: tuple[SlashCommand, ...] = DEFAULT_SLASH_COMMANDS,
    skill_names: list[str] | None = None,
) -> list[PickerCandidate]:
    if buffer.startswith("/"):
        return [
            PickerCandidate(
                label=command.name,
                insert_text=command.name,
                meta=_slash_command_meta(command),
                kind="command",
            )
            for command in match_slash_commands(buffer, commands)
        ]
    if buffer.startswith("$"):
        return [
            PickerCandidate(
                label=f"${name}",
                insert_text=f"${name}",
                meta="Skill reference",
                kind="skill",
            )
            for name in match_skill_mentions(buffer, skill_names or [])
        ]
    return []


def _slash_command_meta(command: SlashCommand) -> str:
    parts = [command.description]
    if command.argument_hint:
        parts.append(command.argument_hint)
    if command.trust_level:
        parts.append(f'trust={command.trust_level}')
    elif command.source:
        parts.append(command.source)
    return ' | '.join(part for part in parts if part)


def prompt_pick_candidate(
    candidates: list[PickerCandidate],
    *,
    title: str,
    initial_index: int = 0,
    theme_setting: str = "auto",
    use_terminal_background: bool = True,
) -> PickerCandidate | None:
    if not candidates:
        return None

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    if not sys.stdin.isatty():
        return None
    if not sys.stdout.isatty():
        return None

    safe_initial_index = max(0, min(initial_index, len(candidates) - 1))
    picker_state = _PickerState(candidates=list(candidates), selected_index=safe_initial_index)
    key_bindings = KeyBindings()

    def _accept(event) -> None:
        current = picker_state.current
        event.app.invalidate()
        event.app.exit(result=current)

    @key_bindings.add("up")
    def _(event) -> None:
        picker_state.move(-1)
        event.app.invalidate()

    @key_bindings.add("down")
    def _(event) -> None:
        picker_state.move(1)
        event.app.invalidate()

    @key_bindings.add("pageup")
    def _(event) -> None:
        picker_state.move_page(-1)
        event.app.invalidate()

    @key_bindings.add("pagedown")
    def _(event) -> None:
        picker_state.move_page(1)
        event.app.invalidate()

    @key_bindings.add("home")
    def _(event) -> None:
        picker_state.move_to_start()
        event.app.invalidate()

    @key_bindings.add("end")
    def _(event) -> None:
        picker_state.move_to_end()
        event.app.invalidate()

    @key_bindings.add("enter")
    def _(event) -> None:
        _accept(event)

    @key_bindings.add("tab")
    def _(event) -> None:
        _accept(event)

    @key_bindings.add("right")
    def _(event) -> None:
        _accept(event)

    @key_bindings.add("escape")
    @key_bindings.add("q")
    def _(event) -> None:
        event.app.invalidate()
        event.app.exit(result=None)

    session = PromptSession(
        reserve_space_for_menu=0,
        key_bindings=key_bindings,
        history=InMemoryHistory(),
        style=Style.from_dict(
            _picker_style_rules(
                theme_setting=theme_setting,
                use_terminal_background=use_terminal_background,
            )
        ),
    )
    session.app.ttimeoutlen = ESCAPE_FLUSH_TIMEOUT_SECONDS
    session.app.timeoutlen = KEY_SEQUENCE_TIMEOUT_SECONDS
    _install_picker_float(session, picker_state, title_getter=lambda: title)
    return session.prompt("")


@dataclass
class _PickerState:
    candidates: list[PickerCandidate]
    selected_index: int
    accepted_text: str | None = None

    @classmethod
    def empty(cls) -> "_PickerState":
        return cls(candidates=[], selected_index=0, accepted_text=None)

    @property
    def visible(self) -> bool:
        return bool(self.candidates)

    @property
    def current(self) -> PickerCandidate | None:
        if not self.candidates:
            return None
        return self.candidates[self.selected_index]

    def move(self, delta: int) -> None:
        if not self.candidates:
            return
        self.selected_index = (self.selected_index + delta) % len(self.candidates)

    def move_page(self, delta: int) -> None:
        if not self.candidates:
            return
        page_size = max(1, min(PICKER_MAX_VISIBLE_CANDIDATES, len(self.candidates)))
        next_index = self.selected_index + (delta * page_size)
        self.selected_index = max(0, min(next_index, len(self.candidates) - 1))

    def move_to_start(self) -> None:
        if not self.candidates:
            return
        self.selected_index = 0

    def move_to_end(self) -> None:
        if not self.candidates:
            return
        self.selected_index = len(self.candidates) - 1

    def replace(self, candidates: list[PickerCandidate], *, reset_selection: bool) -> None:
        self.candidates = candidates
        if not candidates:
            self.selected_index = 0
            return
        if reset_selection or self.selected_index >= len(candidates):
            self.selected_index = 0

    def hide(self) -> None:
        self.candidates = []
        self.selected_index = 0


@dataclass
class _TabCycleState:
    candidates: list[PickerCandidate]
    selected_index: int

    @property
    def current(self) -> PickerCandidate | None:
        if not self.candidates:
            return None
        return self.candidates[self.selected_index]

    def advance(self) -> PickerCandidate | None:
        if not self.candidates:
            return None
        self.selected_index = (self.selected_index + 1) % len(self.candidates)
        return self.current


@dataclass
class _HistoryState:
    entries: list[str]
    browse_index: int | None = None
    draft_text: str = ""

    @property
    def current_entry(self) -> str | None:
        if self.browse_index is None:
            return None
        if not self.entries:
            return None
        return self.entries[self.browse_index]

    def record(self, text: str) -> None:
        if text:
            self.entries.append(text)
        self.reset()

    def reset(self) -> None:
        self.browse_index = None
        self.draft_text = ""

    def move_up(self, current_text: str) -> str | None:
        if not self.entries:
            return None
        if self.browse_index is None:
            self.draft_text = current_text
            self.browse_index = len(self.entries) - 1
            return self.entries[self.browse_index]
        if self.browse_index > 0:
            self.browse_index -= 1
        return self.entries[self.browse_index]

    def move_down(self) -> str | None:
        if self.browse_index is None:
            return None
        if self.browse_index < len(self.entries) - 1:
            self.browse_index += 1
            return self.entries[self.browse_index]
        draft_text = self.draft_text
        self.reset()
        return draft_text


class SlashCommandInput:
    def __init__(
        self,
        commands: tuple[SlashCommand, ...] = DEFAULT_SLASH_COMMANDS,
        *,
        command_provider: CommandProvider | None = None,
        skill_names_provider: Callable[[], list[str]] | None = None,
        theme_setting: str = "auto",
        use_terminal_background: bool = True,
    ) -> None:
        self._commands = commands
        self._command_provider = command_provider
        self._skill_names_provider = skill_names_provider or (lambda: [])
        self._theme_setting = theme_setting
        self._use_terminal_background = use_terminal_background
        self._history_state = _HistoryState(entries=[])
        self._session = self._build_session()

    def _build_session(self):
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.document import Document
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.styles import Style
        except ImportError:
            return None

        def _current_commands() -> tuple[SlashCommand, ...]:
            if self._command_provider is None:
                return self._commands
            return tuple(self._command_provider())

        skill_names_provider = self._skill_names_provider
        history_state = self._history_state
        picker_state = _PickerState.empty()
        tab_cycle_state: _TabCycleState | None = None
        suppress_text_changed = False
        key_bindings = KeyBindings()

        def _set_buffer_text(buffer, text: str) -> None:
            nonlocal suppress_text_changed
            suppress_text_changed = True
            buffer.document = Document(text, len(text))
            suppress_text_changed = False

        def _clear_tab_cycle() -> None:
            nonlocal tab_cycle_state
            tab_cycle_state = None

        def _dismiss_picker() -> None:
            _clear_tab_cycle()
            picker_state.accepted_text = None
            picker_state.hide()

        def _show_candidate(buffer, candidate: PickerCandidate, *, keep_picker_open: bool) -> None:
            _set_buffer_text(buffer, candidate.insert_text)
            if keep_picker_open and tab_cycle_state is not None:
                picker_state.accepted_text = None
                picker_state.replace(list(tab_cycle_state.candidates), reset_selection=False)
                picker_state.selected_index = tab_cycle_state.selected_index
            else:
                picker_state.accepted_text = candidate.insert_text
                picker_state.hide()
            if session.app is not None:
                session.app.invalidate()

        def _should_offer_menu(text: str) -> bool:
            stripped = text.strip()
            return (stripped.startswith("/") or stripped.startswith("$")) and not any(ch.isspace() for ch in stripped)

        def _sync_picker(text: str, *, reset_selection: bool) -> None:
            if picker_state.accepted_text is not None and text == picker_state.accepted_text:
                picker_state.hide()
                if session.app is not None:
                    session.app.invalidate()
                return
            if picker_state.accepted_text is not None and text != picker_state.accepted_text:
                picker_state.accepted_text = None
            if not _should_offer_menu(text):
                picker_state.hide()
                if session.app is not None:
                    session.app.invalidate()
                return
            picker_state.replace(
                build_picker_candidates(text, _current_commands(), skill_names_provider()),
                reset_selection=reset_selection,
            )
            if session.app is not None:
                session.app.invalidate()

        def _accept_current_candidate(buffer, *, submit: bool = False, keep_picker_open: bool = False) -> bool:
            current = picker_state.current
            if current is None:
                return False
            _show_candidate(buffer, current, keep_picker_open=keep_picker_open)
            if submit and _should_submit_selected_candidate(current):
                buffer.validate_and_handle()
            return True

        def _activate_tab_cycle(prefix_text: str) -> bool:
            nonlocal tab_cycle_state
            candidates = build_picker_candidates(prefix_text, _current_commands(), skill_names_provider())
            if len(candidates) <= 1:
                _clear_tab_cycle()
                return False
            current = picker_state.current
            selected_index = 0
            if current is not None:
                for index, candidate in enumerate(candidates):
                    if candidate.insert_text == current.insert_text and candidate.kind == current.kind:
                        selected_index = index
                        break
            tab_cycle_state = _TabCycleState(candidates=list(candidates), selected_index=selected_index)
            return True

        def _cycle_tab_completion(buffer) -> bool:
            if tab_cycle_state is None:
                return False
            current = tab_cycle_state.current
            if current is None or buffer.text != current.insert_text:
                return False
            next_candidate = tab_cycle_state.advance()
            if next_candidate is None:
                return False
            _show_candidate(buffer, next_candidate, keep_picker_open=True)
            return True

        def _sync_picker_selection_into_buffer(buffer) -> None:
            if tab_cycle_state is not None:
                tab_cycle_state.selected_index = picker_state.selected_index
            current = picker_state.current
            if current is not None:
                _set_buffer_text(buffer, current.insert_text)

        @key_bindings.add("up")
        def _(event) -> None:
            buffer = event.current_buffer
            if picker_state.visible:
                picker_state.move(-1)
                if tab_cycle_state is not None:
                    tab_cycle_state.selected_index = picker_state.selected_index
                    current = picker_state.current
                    if current is not None:
                        _set_buffer_text(buffer, current.insert_text)
                event.app.invalidate()
                return
            _clear_tab_cycle()
            previous_text = history_state.move_up(buffer.text)
            if previous_text is not None:
                _set_buffer_text(buffer, previous_text)
                _sync_picker(buffer.text, reset_selection=True)
                event.app.invalidate()

        @key_bindings.add("down")
        def _(event) -> None:
            buffer = event.current_buffer
            if picker_state.visible:
                picker_state.move(1)
                if tab_cycle_state is not None:
                    tab_cycle_state.selected_index = picker_state.selected_index
                    current = picker_state.current
                    if current is not None:
                        _set_buffer_text(buffer, current.insert_text)
                event.app.invalidate()
                return
            _clear_tab_cycle()
            next_text = history_state.move_down()
            if next_text is not None:
                _set_buffer_text(buffer, next_text)
                _sync_picker(buffer.text, reset_selection=True)
                event.app.invalidate()

        @key_bindings.add("pageup")
        def _(event) -> None:
            buffer = event.current_buffer
            if not picker_state.visible:
                return
            picker_state.move_page(-1)
            _sync_picker_selection_into_buffer(buffer)
            event.app.invalidate()

        @key_bindings.add("pagedown")
        def _(event) -> None:
            buffer = event.current_buffer
            if not picker_state.visible:
                return
            picker_state.move_page(1)
            _sync_picker_selection_into_buffer(buffer)
            event.app.invalidate()

        @key_bindings.add("home")
        def _(event) -> None:
            buffer = event.current_buffer
            if not picker_state.visible:
                return
            picker_state.move_to_start()
            _sync_picker_selection_into_buffer(buffer)
            event.app.invalidate()

        @key_bindings.add("end")
        def _(event) -> None:
            buffer = event.current_buffer
            if not picker_state.visible:
                return
            picker_state.move_to_end()
            _sync_picker_selection_into_buffer(buffer)
            event.app.invalidate()

        @key_bindings.add("backspace")
        @key_bindings.add("c-h")
        def _(event) -> None:
            buffer = event.current_buffer
            _clear_tab_cycle()
            history_state.reset()
            if buffer.cursor_position > 0:
                buffer.delete_before_cursor(count=1)

        @key_bindings.add("tab")
        def _(event) -> None:
            buffer = event.current_buffer
            history_state.reset()
            if _cycle_tab_completion(buffer):
                return
            if picker_state.visible:
                _activate_tab_cycle(buffer.text)
                if _accept_current_candidate(buffer, keep_picker_open=tab_cycle_state is not None):
                    return
            if _should_offer_menu(buffer.text):
                if _activate_tab_cycle(buffer.text):
                    candidate = tab_cycle_state.current if tab_cycle_state is not None else None
                    if candidate is not None:
                        _show_candidate(buffer, candidate, keep_picker_open=tab_cycle_state is not None)
                        return
            _clear_tab_cycle()
            if _accept_current_candidate(buffer):
                return
            buffer.insert_text("\t")

        @key_bindings.add("right")
        def _(event) -> None:
            buffer = event.current_buffer
            _clear_tab_cycle()
            history_state.reset()
            if _accept_current_candidate(buffer):
                return
            buffer.cursor_right(count=1)

        @key_bindings.add("enter")
        def _(event) -> None:
            buffer = event.current_buffer
            _clear_tab_cycle()
            history_state.reset()
            if _accept_current_candidate(buffer, submit=True):
                return
            buffer.validate_and_handle()

        @key_bindings.add("escape")
        def _(event) -> None:
            _dismiss_picker()
            history_state.reset()
            event.app.invalidate()

        session = PromptSession(
            reserve_space_for_menu=0,
            key_bindings=key_bindings,
            history=InMemoryHistory(),
            style=Style.from_dict(
                _picker_style_rules(
                    theme_setting=self._theme_setting,
                    use_terminal_background=self._use_terminal_background,
                )
            ),
        )
        session.app.ttimeoutlen = ESCAPE_FLUSH_TIMEOUT_SECONDS
        session.app.timeoutlen = KEY_SEQUENCE_TIMEOUT_SECONDS
        buffer = session.default_buffer

        def _auto_start_completion(_buffer) -> None:
            if suppress_text_changed:
                return
            if history_state.browse_index is not None and _buffer.text != history_state.current_entry:
                history_state.reset()
            if tab_cycle_state is not None:
                current = tab_cycle_state.current
                if current is None or _buffer.text != current.insert_text:
                    _clear_tab_cycle()
            _sync_picker(_buffer.text, reset_selection=True)

        buffer.on_text_changed += _auto_start_completion
        _install_picker_float(
            session,
            picker_state,
            title_getter=lambda: "Commands"
            if picker_state.current and picker_state.current.kind == "command"
            else "Skills",
        )
        return session

    def read_message(self, prompt: str = "❯ ") -> str:
        if self._session is None:
            return input(prompt)
        text = self._session.prompt(prompt)
        if text:
            self._history_state.record(text)
        return text


def read_user_message(
    prompt: str = "❯ ",
    commands: tuple[SlashCommand, ...] = DEFAULT_SLASH_COMMANDS,
) -> str:
    return SlashCommandInput(commands).read_message(prompt)


def _should_submit_selected_candidate(candidate: PickerCandidate) -> bool:
    return candidate.kind == "command"


def _picker_visible_window(picker_state: _PickerState) -> tuple[int, int]:
    if not picker_state.candidates:
        return 0, 0
    visible_count = min(PICKER_MAX_VISIBLE_CANDIDATES, len(picker_state.candidates))
    max_start = max(0, len(picker_state.candidates) - visible_count)
    centered_start = picker_state.selected_index - (visible_count // 2)
    start = max(0, min(centered_start, max_start))
    end = start + visible_count
    return start, end


def _visible_picker_candidates(picker_state: _PickerState) -> list[tuple[int, PickerCandidate]]:
    if not picker_state.candidates:
        return []
    start, end = _picker_visible_window(picker_state)
    return list(enumerate(picker_state.candidates[start:end], start=start))


def _render_picker_menu_fragments(
    picker_state: _PickerState,
    *,
    title: str,
) -> list[tuple[str, str]]:
    menu_width = _picker_menu_width(picker_state, title=title)
    start, end = _picker_visible_window(picker_state)
    fragments: list[tuple[str, str]] = [
        ("class:picker.title", _pad_picker_line(title, menu_width) + "\n"),
    ]
    top_marker = "↑ more" if start > 0 else ""
    fragments.append(("class:picker.more", _pad_picker_line(top_marker, menu_width) + "\n"))
    for candidate_index, candidate in _visible_picker_candidates(picker_state):
        is_selected = candidate_index == picker_state.selected_index
        prefix = "› " if is_selected else "  "
        suffix = "  (current)" if is_selected else ""
        label = f"{' ' * PICKER_LABEL_INDENT}{prefix}{candidate.label}{suffix}"
        line_style = "class:picker.selected" if is_selected else "class:picker.item"
        fragments.append((line_style, _pad_picker_line(label, menu_width) + "\n"))
        if candidate.meta:
            meta = f"{' ' * PICKER_META_INDENT}{candidate.meta}"
            meta_style = "class:picker.selected-meta" if is_selected else "class:picker.meta"
            fragments.append((meta_style, _pad_picker_line(meta, menu_width) + "\n"))
    bottom_marker = "↓ more" if end < len(picker_state.candidates) else ""
    fragments.append(("class:picker.more", _pad_picker_line(bottom_marker, menu_width) + "\n"))
    return fragments


def _picker_menu_height(picker_state: _PickerState) -> int:
    lines = 2
    _start, _end = _picker_visible_window(picker_state)
    for _candidate_index, candidate in _visible_picker_candidates(picker_state):
        lines += 2 if candidate.meta else 1
    return max(lines, 1)


def _picker_menu_width(picker_state: _PickerState, *, title: str) -> int:
    widths = [len(title)]
    start, end = _picker_visible_window(picker_state)
    if start > 0:
        widths.append(len("↑ more"))
    for _candidate_index, candidate in _visible_picker_candidates(picker_state):
        widths.append(len(f"{' ' * PICKER_LABEL_INDENT}› {candidate.label}  (current)"))
        if candidate.meta:
            widths.append(len(f"{' ' * PICKER_META_INDENT}{candidate.meta}"))
    if end < len(picker_state.candidates):
        widths.append(len("↓ more"))
    content_width = max(widths, default=20)
    return max(PICKER_MIN_WIDTH, min(content_width + (PICKER_OUTER_PADDING * 2), PICKER_MAX_WIDTH))


def _pad_picker_line(text: str, width: int) -> str:
    padded = f"{' ' * PICKER_OUTER_PADDING}{text}"
    return padded.ljust(width)


def _picker_style_rules(
    *,
    theme_setting: str = "auto",
    use_terminal_background: bool = True,
) -> dict[str, str]:
    theme_name = resolve_theme_setting(theme_setting)
    tokens = get_ui_tokens(theme_name, use_terminal_background=use_terminal_background)
    return build_prompt_toolkit_picker_style(tokens)


def _find_float_container(container):
    try:
        from prompt_toolkit.layout.containers import FloatContainer
    except ImportError:
        return None
    if isinstance(container, FloatContainer):
        return container
    for attribute in ("content", "alternative_content"):
        child = getattr(container, attribute, None)
        if child is not None:
            located = _find_float_container(child)
            if located is not None:
                return located
    for child in getattr(container, "children", []):
        located = _find_float_container(child)
        if located is not None:
            return located
    return None


def _install_picker_float(
    session,
    picker_state: _PickerState,
    *,
    title_getter: Callable[[], str],
) -> None:
    try:
        from prompt_toolkit.filters import Condition, is_done
        from prompt_toolkit.layout.containers import ConditionalContainer, Float, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
    except ImportError:
        return

    float_container = _find_float_container(session.layout.container)
    if float_container is None:
        return

    menu_window = Window(
        FormattedTextControl(lambda: _render_picker_menu_fragments(picker_state, title=title_getter())),
        dont_extend_width=True,
        dont_extend_height=True,
        style="class:picker.menu",
        wrap_lines=False,
    )
    menu_container = ConditionalContainer(
        menu_window,
        filter=Condition(lambda: picker_state.visible) & ~is_done,
    )
    float_container.floats.append(
        Float(
            xcursor=True,
            ycursor=True,
            left=0,
            top=1,
            allow_cover_cursor=False,
            z_index=20,
            width=lambda: _picker_menu_width(picker_state, title=title_getter()),
            height=lambda: _picker_menu_height(picker_state),
            content=menu_container,
        )
    )
