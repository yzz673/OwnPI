from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_academic_paper_writing_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='academic-paper-writing',
            name='academic-paper-writing',
            description='Author, revise, diagnose, and assemble evidence-grounded academic papers.',
            tags=('Research', 'Writing'),
            when_to_use=(
                'Use for academic manuscript arguments, sections, claim-evidence-citation alignment, '
                'figure/table narratives, revision, or submission preparation.'
            ),
            aliases=(),
        )
    )
