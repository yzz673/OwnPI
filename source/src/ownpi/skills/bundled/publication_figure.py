from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_publication_figure_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='publication-figure',
            name='publication-figure',
            description='Create, revise, and audit submission-grade scientific figures with Python or R.',
            tags=('Figures', 'Visualization', 'Research'),
            when_to_use='Use for manuscript figures, multi-panel scientific plots, publication visualizations, figure revision, or figure quality review.',
            aliases=('scientific figure', 'paper figure', '论文配图', '科研绘图'),
        )
    )
