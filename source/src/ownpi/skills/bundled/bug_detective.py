from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_bug_detective_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='bug-detective',
            name='bug-detective',
            description='Investigate bugs from symptoms to root cause with focused reproduction and verification.',
            tags=('Development', 'Debugging'),
            when_to_use='Use when debugging errors, regressions, failing tests, unexpected behavior, or unclear runtime failures.',
            aliases=('debugging', 'bug investigation', 'root cause analysis'),
        )
    )
