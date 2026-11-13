from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_results_analysis_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='results-analysis',
            name='results-analysis',
            description='Apply evidence-first methods for rigorous experimental results analysis.',
            when_to_use='Use when experimental evidence needs validation, comparison design, statistical analysis, scientific visualization, or evidence-bounded interpretation.',
            aliases=('experiment analysis', 'strict results analysis'),
        )
    )
