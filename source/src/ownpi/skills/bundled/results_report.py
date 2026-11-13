from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_results_report_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='results-report',
            name='results-report',
            description='Turn completed experiment artifacts into a decision-oriented research report.',
            when_to_use='Use when the user wants to turn completed experiments into a structured report, retrospective, or next-action summary.',
            aliases=('experiment report', 'results writeup'),
        )
    )
