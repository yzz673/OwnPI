from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_git_workflow_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='git-workflow',
            name='git-workflow',
            description='Manage git branches, commits, pull requests, and review-ready change stacks.',
            tags=('Development', 'Git'),
            when_to_use='Use when preparing commits, splitting changes, reviewing branch state, or managing PR workflow.',
            aliases=('git', 'commit workflow', 'pull request workflow'),
        )
    )
