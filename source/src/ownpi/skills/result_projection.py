"""Shrink skills catalog deliveries while preserving their paging coordinates."""

from __future__ import annotations

import json


def reduce_skills_list(text: str, byte_limit: int) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(value, dict):
        return text
    data = value.get('data', {})
    skills = data.get('skills')
    if not isinstance(skills, list):
        return text
    while len(skills) > 1:
        skills = skills[:max(1, len(skills) // 2)]
        data.update(skills=skills, next_cursor=int(data['cursor']) + len(skills))
        value.pop('stdout', None)
        rendered = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        if len(rendered.encode('utf-8')) <= byte_limit:
            return rendered
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
