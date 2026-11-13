from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_daily_coding_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='daily-coding',
            name='daily-coding',
            description='Handle everyday coding tasks with scoped edits, local patterns, and targeted verification.',
            tags=('Development',),
            when_to_use='Use for ordinary source-code changes, small feature work, refactors, and test-backed fixes.',
            aliases=('coding task', 'code change', 'implementation'),
        )
    )
