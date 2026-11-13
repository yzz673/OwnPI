from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_session_wrap_up_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='session-wrap-up',
            name='session-wrap-up',
            description=(
                'Generate session work log, check for AGENTS.md updates, and clean up '
                'temporary files. Replaces session-summary and stop-summary hooks.'
            ),
            tags=(),
        )
    )
