from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ownpi.chat.context_meter import (
    DEFAULT_CONTEXT_METER_WIDTH,
    format_context_meter_details,
    render_context_meter_bar,
)
from ownpi.chat.theme import (
    UISemanticTokens,
    build_rich_panel_theme,
    get_ui_tokens,
    resolve_theme_setting,
)


def resolve_context_meter_surface_mode(
    *,
    enabled: bool,
    mode: str,
    stdout_isatty: bool,
    stdin_isatty: bool,
    rich_available: bool,
) -> str:
    normalized_mode = str(mode or 'auto').strip().lower() or 'auto'
    if not enabled:
        return 'disabled'
    if normalized_mode == 'static_only':
        return 'static_only'
    if normalized_mode not in {'auto', 'live'}:
        normalized_mode = 'auto'
    tty_ready = stdout_isatty and stdin_isatty
    if rich_available and tty_ready:
        return 'live'
    return 'static_only'


_SPINNER_FRAMES = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')


def spinner_glyph_for_elapsed(elapsed_ms: float | int = 0.0) -> str:
    elapsed = max(0.0, float(elapsed_ms or 0.0))
    index = int(elapsed / 120.0) % len(_SPINNER_FRAMES)
    return _SPINNER_FRAMES[index]


def render_thinking_status_line(
    *,
    elapsed_ms: float | int = 0.0,
    label: str = 'Thinking',
    spinner_glyph: str | None = None,
) -> str:
    elapsed_seconds = max(0.0, float(elapsed_ms or 0.0) / 1000.0)
    prefix = f'{spinner_glyph} ' if spinner_glyph else ''
    return f'{prefix}{label}… {elapsed_seconds:.1f}s'


@dataclass
class LiveRuntimeStatus:
    enabled: bool
    surface_mode: str = 'disabled'
    ui_mode: str = 'user'
    meter_width: int = DEFAULT_CONTEXT_METER_WIDTH
    show_threshold: bool = True
    theme_tokens: UISemanticTokens = field(default_factory=lambda: get_ui_tokens('dark'))
    _console: Any | None = None
    _live: Any | None = None
    _state: dict[str, Any] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _started_at_monotonic: float | None = None
    _ticker_stop: threading.Event | None = None
    _ticker_thread: threading.Thread | None = None

    @classmethod
    def create(
        cls,
        *,
        enabled: bool,
        surface_mode: str,
        ui_mode: str,
        meter_width: int = DEFAULT_CONTEXT_METER_WIDTH,
        show_threshold: bool = True,
        theme_setting: str = 'auto',
        use_terminal_background: bool = True,
    ) -> 'LiveRuntimeStatus':
        tokens = get_ui_tokens(
            resolve_theme_setting(theme_setting),
            use_terminal_background=use_terminal_background,
        )
        if not enabled:
            return cls(
                enabled=False,
                surface_mode=surface_mode,
                ui_mode=ui_mode,
                meter_width=meter_width,
                show_threshold=show_threshold,
                theme_tokens=tokens,
            )
        try:
            from rich.console import Console
            from rich.live import Live
        except ImportError:
            return cls(
                enabled=False,
                surface_mode=surface_mode,
                ui_mode=ui_mode,
                meter_width=meter_width,
                show_threshold=show_threshold,
                theme_tokens=tokens,
            )
        console = Console()
        live = Live(console=console, auto_refresh=False, transient=True)
        return cls(
            enabled=True,
            surface_mode=surface_mode,
            ui_mode=ui_mode,
            meter_width=meter_width,
            show_threshold=show_threshold,
            theme_tokens=tokens,
            _console=console,
            _live=live,
        )

    def __enter__(self) -> 'LiveRuntimeStatus':
        if self.enabled and self._live is not None:
            with self._lock:
                if self._started_at_monotonic is None:
                    self._started_at_monotonic = time.monotonic()
            self._live.start()
            self._start_ticker()
            self._refresh()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop_ticker()
        if self.enabled and self._live is not None:
            self._live.stop()

    def update(self, payload: dict[str, Any] | None) -> None:
        if self.surface_mode == 'disabled' or not isinstance(payload, dict):
            return
        now = time.monotonic()
        with self._lock:
            self._state.update({key: value for key, value in payload.items() if value is not None})
            if self._started_at_monotonic is None:
                self._started_at_monotonic = now
        if self.enabled:
            self._refresh()

    def print_event(self, message: str) -> None:
        if not self.enabled or self._console is None:
            return
        self._console.print(message)

    def render_text(self) -> str:
        with self._lock:
            state = dict(self._state)
        context_meter = (
            dict(state.get('context_meter_summary', {}))
            if isinstance(state.get('context_meter_summary'), dict)
            else {}
        )
        stage = str(state.get('stage', '') or context_meter.get('display_stage', '') or '<unknown>')
        attempt = int(state.get('attempt', 0) or context_meter.get('display_attempt', 0) or 0)
        query_chain_id = str(state.get('query_chain_id', '') or context_meter.get('query_chain_id', '') or '<none>')
        continuation = int(
            state.get('continuation_index', 0)
            or context_meter.get('continuation_index', 0)
            or 0
        )
        provider = str(state.get('provider', '') or 'unknown')
        component = str(state.get('component', '') or 'unknown')
        operation = str(
            state.get('operation', '')
            or context_meter.get('provider_operation', '')
            or 'unknown'
        )
        elapsed_ms = int(
            state.get('elapsed_ms', 0)
            or context_meter.get('provider_elapsed_ms', 0)
            or 0
        )
        heartbeat_count = int(state.get('heartbeat_count', 0) or 0)
        inflight = bool(
            state.get('provider_inflight', context_meter.get('provider_inflight', False))
        )
        if elapsed_ms <= 0 and self._started_at_monotonic is not None:
            elapsed_ms = int(max(0.0, (time.monotonic() - self._started_at_monotonic) * 1000.0))
        thinking_line = render_thinking_status_line(
            elapsed_ms=elapsed_ms,
            label=str(state.get('label', 'Thinking') or 'Thinking'),
            spinner_glyph=spinner_glyph_for_elapsed(elapsed_ms) if self.surface_mode == 'live' else None,
        )
        if self.ui_mode != 'developer':
            lines = [thinking_line]
            if stage and stage != '<unknown>':
                lines.append(f'Stage: {stage}')
            if operation and operation != 'unknown':
                lines.append(f'Operation: {operation}')
            return '\n'.join(lines)
        bar = render_context_meter_bar(
            context_meter,
            width=self.meter_width,
            show_threshold=self.show_threshold,
        )
        lines = [
            thinking_line,
            f"stage={stage} attempt={attempt} query_chain={query_chain_id} continuation={continuation}",
            (
                f"provider={provider} component={component} operation={operation} "
                f"elapsed_ms={elapsed_ms} heartbeat={heartbeat_count} inflight={'yes' if inflight else 'no'}"
            ),
            bar,
            format_context_meter_details(context_meter),
        ]
        return '\n'.join(lines)

    def _refresh(self) -> None:
        if not self.enabled or self._live is None:
            return
        try:
            from rich.panel import Panel
            from rich.text import Text
        except ImportError:
            return
        with self._lock:
            rich_theme = build_rich_panel_theme(self.theme_tokens, ui_mode=self.ui_mode)
            rendered = Text(self.render_text(), style=rich_theme.text_style)
            title = 'Context Meter' if self.ui_mode == 'developer' else 'Thinking'
            self._live.update(
                Panel(
                    rendered,
                    title=Text(title, style=rich_theme.title_style),
                    border_style=rich_theme.border_style,
                ),
                refresh=True,
            )

    def _start_ticker(self) -> None:
        if not self.enabled or self._live is None:
            return
        if self._ticker_thread is not None and self._ticker_thread.is_alive():
            return
        self._ticker_stop = threading.Event()

        def _ticker_loop() -> None:
            while self._ticker_stop is not None and not self._ticker_stop.wait(0.1):
                try:
                    self._refresh()
                except Exception:
                    continue

        self._ticker_thread = threading.Thread(target=_ticker_loop, daemon=True, name='ownpi-live-status')
        self._ticker_thread.start()

    def _stop_ticker(self) -> None:
        if self._ticker_stop is not None:
            self._ticker_stop.set()
        if self._ticker_thread is not None and self._ticker_thread.is_alive():
            self._ticker_thread.join(timeout=0.2)
        self._ticker_stop = None
        self._ticker_thread = None


@dataclass
class DirectThinkingStatus:
    enabled: bool
    surface_mode: str = 'disabled'
    ui_mode: str = 'user'
    theme_tokens: UISemanticTokens = field(default_factory=lambda: get_ui_tokens('dark'))
    _console: Any | None = None
    _live: Any | None = None
    _state: dict[str, Any] = field(
        default_factory=lambda: {
            'label': 'Thinking',
            'elapsed_ms': 0.0,
            'detail': None,
        }
    )
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @classmethod
    def create(
        cls,
        *,
        enabled: bool,
        surface_mode: str,
        ui_mode: str,
        theme_setting: str = 'auto',
        use_terminal_background: bool = True,
    ) -> 'DirectThinkingStatus':
        tokens = get_ui_tokens(
            resolve_theme_setting(theme_setting),
            use_terminal_background=use_terminal_background,
        )
        if not enabled:
            return cls(enabled=False, surface_mode=surface_mode, ui_mode=ui_mode, theme_tokens=tokens)
        try:
            from rich.console import Console
            from rich.live import Live
        except ImportError:
            return cls(enabled=False, surface_mode=surface_mode, ui_mode=ui_mode, theme_tokens=tokens)
        console = Console()
        live = Live(console=console, auto_refresh=False, transient=True)
        return cls(
            enabled=True,
            surface_mode=surface_mode,
            ui_mode=ui_mode,
            theme_tokens=tokens,
            _console=console,
            _live=live,
        )

    def __enter__(self) -> 'DirectThinkingStatus':
        if self.enabled and self._live is not None:
            self._live.start()
            self._refresh()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.enabled and self._live is not None:
            self._live.stop()

    def update(
        self,
        *,
        elapsed_ms: float,
        label: str = 'Thinking',
        detail: str | None = None,
    ) -> None:
        with self._lock:
            self._state.update(
                {
                    'elapsed_ms': float(elapsed_ms or 0.0),
                    'label': str(label or 'Thinking'),
                    'detail': str(detail).strip() if detail is not None else None,
                }
            )
        if self.enabled:
            self._refresh()

    def render_text(self) -> str:
        with self._lock:
            state = dict(self._state)
        line = render_thinking_status_line(
            elapsed_ms=float(state.get('elapsed_ms', 0.0) or 0.0),
            label=str(state.get('label', 'Thinking') or 'Thinking'),
            spinner_glyph=spinner_glyph_for_elapsed(state.get('elapsed_ms', 0.0) or 0.0) if self.surface_mode == 'live' else None,
        )
        detail = str(state.get('detail', '') or '').strip()
        if self.ui_mode == 'developer' and detail:
            return f'{line}\nmode={detail}'
        return line

    def _refresh(self) -> None:
        if not self.enabled or self._live is None:
            return
        try:
            from rich.panel import Panel
            from rich.text import Text
        except ImportError:
            return
        with self._lock:
            rich_theme = build_rich_panel_theme(self.theme_tokens, ui_mode=self.ui_mode)
            rendered = Text(self.render_text(), style=rich_theme.text_style)
            self._live.update(
                Panel(
                    rendered,
                    title=Text('Thinking', style=rich_theme.title_style),
                    border_style=rich_theme.border_style,
                ),
                refresh=True,
            )


def build_live_runtime_status(
    *,
    enabled: bool,
    mode: str,
    ui_mode: str,
    meter_width: int = DEFAULT_CONTEXT_METER_WIDTH,
    show_threshold: bool = True,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> LiveRuntimeStatus:
    rich_available = _rich_live_available()
    surface_mode = resolve_context_meter_surface_mode(
        enabled=enabled,
        mode=mode,
        stdout_isatty=sys.stdout.isatty(),
        stdin_isatty=sys.stdin.isatty(),
        rich_available=rich_available,
    )
    return LiveRuntimeStatus.create(
        enabled=surface_mode == 'live',
        surface_mode=surface_mode,
        ui_mode=ui_mode,
        meter_width=meter_width,
        show_threshold=show_threshold,
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )


def build_direct_thinking_status(
    *,
    ui_mode: str,
    theme_setting: str = 'auto',
    use_terminal_background: bool = True,
) -> DirectThinkingStatus:
    rich_available = _rich_live_available()
    surface_mode = resolve_context_meter_surface_mode(
        enabled=True,
        mode='auto',
        stdout_isatty=sys.stdout.isatty(),
        stdin_isatty=sys.stdin.isatty(),
        rich_available=rich_available,
    )
    return DirectThinkingStatus.create(
        enabled=surface_mode == 'live',
        surface_mode=surface_mode,
        ui_mode=ui_mode,
        theme_setting=theme_setting,
        use_terminal_background=use_terminal_background,
    )


def _rich_live_available() -> bool:
    try:
        from rich.console import Console  # noqa: F401
        from rich.live import Live  # noqa: F401
    except ImportError:
        return False
    return True
