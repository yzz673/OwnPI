from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_ai_model_guide_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='ai-model-guide',
            name='ai-model-guide',
            description='',  # The catalog description is loaded from SKILL.md.
            tags=('Research', 'Modeling'),
        )
    )
