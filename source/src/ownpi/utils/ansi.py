from __future__ import annotations

import os
import re
import sys

_RESET = "\033[0m"
_BOLD = "\033[1m"
_COLORS = {
    "blue": "\033[94m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "gray": "\033[90m",
    "magenta": "\033[95m",
    "yellow": "\033[93m",
}


def supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def style_text(text: str, *, color: str | None = None, bold: bool = False) -> str:
    if not supports_color():
        return text
    prefix = ""
    if bold:
        prefix += _BOLD
    if color is not None:
        prefix += _ansi_color_code(color)
    if not prefix:
        return text
    return f"{prefix}{text}{_RESET}"


def _ansi_color_code(color: str) -> str:
    named = _COLORS.get(color)
    if named is not None:
        return named
    hex_match = re.match(r"^#([0-9a-fA-F]{6})$", color)
    if not hex_match:
        return ""
    raw = hex_match.group(1)
    red = int(raw[0:2], 16)
    green = int(raw[2:4], 16)
    blue = int(raw[4:6], 16)
    return f"\033[38;2;{red};{green};{blue}m"
