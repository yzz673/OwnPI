from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_feedback_authoring_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='feedback-authoring',
            name='feedback-authoring',
            description=(
                'Write concise findings-first feedback for a research-stage review.'
            ),
            tags=('Review', 'Research', 'Runtime'),
            when_to_use=(
                'Runtime injects this skill for Reviewer and Analysis feedback.'
            ),
            aliases=('feedback authoring', 'research feedback'),
            disable_model_invocation=True,
        )
    )
