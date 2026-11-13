from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_code_review_excellence_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='code-review-excellence',
            name='code-review-excellence',
            description='Review diffs and pull requests for bugs, regressions, missing tests, and contract risks.',
            tags=('Development', 'Review'),
            when_to_use='Use when reviewing code changes, pull requests, staged commits, or architecture-sensitive diffs.',
            aliases=('code review', 'PR review', 'diff review'),
        )
    )
