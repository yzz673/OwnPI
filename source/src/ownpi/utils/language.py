from __future__ import annotations

import re

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def contains_chinese(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


def detect_response_language(text: str) -> str:
    return "Chinese" if contains_chinese(text) else "English"


def build_response_language_instruction(text: str) -> str:
    if detect_response_language(text) == "Chinese":
        return (
            "Respond in Chinese. Even if the source material is in English, keep the final answer in Chinese "
            "unless the user explicitly asks for another language."
        )
    return "Respond in English unless the user explicitly asks for another language."
