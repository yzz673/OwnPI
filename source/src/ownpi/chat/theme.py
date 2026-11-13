from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Literal, Mapping


ThemeSetting = Literal['auto', 'light', 'dark']
ResolvedThemeName = Literal['light', 'dark']


@dataclass(frozen=True)
class UISemanticTokens:
    text: str
    inverse_text: str
    text_muted: str
    text_title: str
    accent: str
    accent_soft: str
    focus: str
    border: str
    success: str
    warning: str
    error: str
    surface_bg: str | None = None
    elevated_bg: str | None = None
    shadow_bg: str | None = None


_DARK_TOKENS = UISemanticTokens(
    text='#e5e7eb',
    inverse_text='#111111',
    text_muted='#94a3b8',
    text_title='#c4b5fd',
    accent='#a78bfa',
    accent_soft='#8b5cf6',
    focus='#c4b5fd',
    border='#64748b',
    success='#34d399',
    warning='#fbbf24',
    error='#f87171',
    surface_bg='#111827',
    elevated_bg='#0f172a',
    shadow_bg='#020617',
)

_LIGHT_TOKENS = UISemanticTokens(
    text='#111111',
    inverse_text='#ffffff',
    text_muted='#374151',
    text_title='#4c1d95',
    accent='#6d28d9',
    accent_soft='#7c3aed',
    focus='#7c3aed',
    border='#94a3b8',
    success='#15803d',
    warning='#b45309',
    error='#b91c1c',
    surface_bg='#ffffff',
    elevated_bg='#f8fafc',
    shadow_bg='#e2e8f0',
)


def detect_theme_from_colorfgbg(
    env: Mapping[str, str] | None = None,
) -> ResolvedThemeName | None:
    resolved_env = env or os.environ
    colorfgbg = str(resolved_env.get('COLORFGBG', '') or '').strip()
    if not colorfgbg:
        return None
    parts = colorfgbg.split(';')
    bg = parts[-1].strip() if parts else ''
    if not bg:
        return None
    try:
        bg_num = int(bg)
    except ValueError:
        return None
    if bg_num < 0 or bg_num > 15:
        return None
    return 'dark' if bg_num <= 6 or bg_num == 8 else 'light'


def theme_from_osc11_response(data: str) -> ResolvedThemeName | None:
    rgb = _parse_osc_rgb(data)
    if rgb is None:
        return None
    red, green, blue = rgb
    luminance = (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)
    return 'light' if luminance > 0.5 else 'dark'


def resolve_theme_setting(
    setting: str,
    *,
    env: Mapping[str, str] | None = None,
) -> ResolvedThemeName:
    normalized = str(setting or 'auto').strip().lower() or 'auto'
    if normalized in {'light', 'dark'}:
        return normalized
    return detect_theme_from_colorfgbg(env) or 'dark'


def get_ui_tokens(
    theme: str,
    *,
    use_terminal_background: bool = True,
) -> UISemanticTokens:
    resolved = 'light' if str(theme or '').strip().lower() == 'light' else 'dark'
    tokens = _LIGHT_TOKENS if resolved == 'light' else _DARK_TOKENS
    if not use_terminal_background:
        return tokens
    return UISemanticTokens(
        text='default',
        inverse_text=tokens.inverse_text,
        text_muted=tokens.text_muted,
        text_title=tokens.text_title,
        accent=tokens.accent,
        accent_soft=tokens.accent_soft,
        focus=tokens.focus,
        border=tokens.border,
        success=tokens.success,
        warning=tokens.warning,
        error=tokens.error,
        surface_bg=None,
        elevated_bg=None,
        shadow_bg=None,
    )


def build_prompt_toolkit_dialog_style(tokens: UISemanticTokens) -> dict[str, str]:
    rules = {
        'dialog': _pt_style(fg=tokens.text, bg=tokens.surface_bg),
        'dialog.body': _pt_style(fg=tokens.text, bg=tokens.surface_bg),
        'dialog frame.label': _pt_style(fg=tokens.text_title, bg=tokens.elevated_bg, bold=True),
        'dialog-title': _pt_style(fg=tokens.text_title, bold=True),
        'frame.border': _pt_style(fg=tokens.border),
        'button': _pt_style(fg=tokens.text, bold=True),
        'button focused': _pt_style(fg=tokens.inverse_text, bg=tokens.accent_soft, bold=True),
        'radio': _pt_style(fg=tokens.text),
        'radio-selected': _pt_style(fg=tokens.accent, bold=True),
        'checkbox': _pt_style(fg=tokens.text),
        'checkbox-selected': _pt_style(fg=tokens.accent, bold=True),
        'text-area': _pt_style(fg=tokens.text, bg=tokens.surface_bg),
        'dialog shadow': _pt_style(bg=tokens.shadow_bg),
    }
    return {key: value for key, value in rules.items() if value}


def build_prompt_toolkit_picker_style(tokens: UISemanticTokens) -> dict[str, str]:
    rules = {
        'picker.menu': _pt_style(fg=tokens.text, bg=tokens.elevated_bg),
        'picker.title': _pt_style(fg=tokens.text_title, bold=True),
        'picker.item': _pt_style(fg=tokens.text),
        'picker.meta': _pt_style(fg=tokens.text_muted),
        'picker.more': _pt_style(fg=tokens.border, bold=True),
        'picker.selected': _pt_style(fg=tokens.inverse_text, bg=tokens.accent_soft, bold=True),
        'picker.selected-meta': _pt_style(fg=tokens.inverse_text, bg=tokens.accent_soft),
    }
    return {key: value for key, value in rules.items() if value}


@dataclass(frozen=True)
class RichPanelTheme:
    border_style: str
    title_style: str
    text_style: str


def build_rich_panel_theme(tokens: UISemanticTokens, *, ui_mode: str) -> RichPanelTheme:
    normalized_ui_mode = str(ui_mode or 'user').strip().lower() or 'user'
    border = tokens.border if normalized_ui_mode == 'developer' else tokens.accent
    title = tokens.text_title
    text = tokens.text
    return RichPanelTheme(border_style=border, title_style=title, text_style=text)


def build_rich_markdown_styles(tokens: UISemanticTokens) -> dict[str, str]:
    return {
        'markdown.paragraph': tokens.text,
        'markdown.text': tokens.text,
        'markdown.h1': f'bold {tokens.text_title}',
        'markdown.h2': f'bold {tokens.accent}',
        'markdown.h3': f'bold {tokens.accent_soft}',
        'markdown.h4': f'bold {tokens.text}',
        'markdown.h5': f'bold {tokens.text}',
        'markdown.h6': f'bold {tokens.text}',
        'markdown.item': tokens.text,
        'markdown.item.bullet': tokens.accent,
        'markdown.item.number': tokens.accent,
        'markdown.link': f'underline {tokens.accent}',
        'markdown.link_url': f'underline {tokens.accent_soft}',
        'markdown.code': tokens.accent_soft,
        'markdown.code_block': tokens.text,
        'markdown.block_quote': tokens.text_muted,
        'markdown.hr': tokens.border,
        'markdown.table.border': tokens.border,
        'markdown.table.header': f'bold {tokens.text_title}',
        'markdown.strong': f'bold {tokens.text}',
        'markdown.em': f'italic {tokens.text_muted}',
    }


def _pt_style(
    *,
    fg: str | None = None,
    bg: str | None = None,
    bold: bool = False,
    underline: bool = False,
) -> str:
    parts: list[str] = []
    if fg:
        parts.append(f'fg:{fg}')
    if bg:
        parts.append(f'bg:{bg}')
    if bold:
        parts.append('bold')
    if underline:
        parts.append('underline')
    return ' '.join(parts)


def _parse_osc_rgb(data: str) -> tuple[float, float, float] | None:
    normalized = str(data or '').strip()
    rgb_match = re.match(r'^rgba?:([0-9a-f]{1,4})/([0-9a-f]{1,4})/([0-9a-f]{1,4})', normalized, re.IGNORECASE)
    if rgb_match:
        return tuple(_hex_component(value) for value in rgb_match.groups())  # type: ignore[return-value]
    hash_match = re.match(r'^#([0-9a-f]+)$', normalized, re.IGNORECASE)
    if hash_match:
        hex_value = hash_match.group(1)
        if len(hex_value) % 3 != 0:
            return None
        chunk = len(hex_value) // 3
        return (
            _hex_component(hex_value[0:chunk]),
            _hex_component(hex_value[chunk : chunk * 2]),
            _hex_component(hex_value[chunk * 2 : chunk * 3]),
        )
    return None


def _hex_component(value: str) -> float:
    max_value = (16 ** len(value)) - 1
    if max_value <= 0:
        return 0.0
    return int(value, 16) / max_value
