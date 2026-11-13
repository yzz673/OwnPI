from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurn:
    user_message: str
    run_dir: str = ""


__all__ = ["ChatTurn"]
