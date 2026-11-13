from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_frontend_design_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='frontend-design',
            name='frontend-design',
            description='Design and build distinctive, production-grade frontend interfaces.',
            tags=('Development', 'Design'),
            when_to_use='Use when building or revising web UI, dashboards, components, app layouts, or frontend experiences.',
            aliases=('frontend UI', 'web design', 'interface design'),
        )
    )
