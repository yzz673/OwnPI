from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_post_acceptance_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='post-acceptance',
            name='post-acceptance',
            description=(
                'This skill should be used when the user asks to "prepare conference '
                'presentation", "create presentation slides", "design poster", "make '
                'academic poster", "write promotion content", "create Twitter thread", or '
                'mentions post-acceptance conference preparation. Provides comprehensive '
                'workflow for presentation, poster, and promotion content creation.'
            ),
            tags=(),
        )
    )
