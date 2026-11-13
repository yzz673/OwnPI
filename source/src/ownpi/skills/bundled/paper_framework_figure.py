from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_paper_framework_figure_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='paper-framework-figure',
            name='paper-framework-figure',
            description='Create editable paper framework diagrams with python-pptx.',
            tags=('Research', 'Writing', 'Visualization'),
            when_to_use=(
                'Use for editable model architectures, method pipelines, and paper '
                'framework diagrams with PDF export and visual inspection.'
            ),
            aliases=('model architecture diagram', 'framework diagram', 'editable paper figure'),
        )
    )
