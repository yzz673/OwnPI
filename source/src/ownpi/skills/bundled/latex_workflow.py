from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_latex_workflow_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='latex-workflow',
            name='latex-workflow',
            description='Author, build, revise, and validate LaTeX papers without unnecessary recompilation.',
            tags=('Research', 'Writing', 'LaTeX'),
            when_to_use='Use for LaTeX manuscript authoring, compilation checkpoints, PDF inspection, revision, or final document closure.',
            aliases=('latex paper', 'latex manuscript', 'paper compilation'),
        )
    )
