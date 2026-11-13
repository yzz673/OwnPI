from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_ui_ux_pro_max_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='ui-ux-pro-max',
            name='ui-ux-pro-max',
            description='Design or review UI with product-appropriate interaction, layout, accessibility, and visual polish.',
            tags=('Frontend', 'Design'),
            when_to_use='Use when designing, reviewing, or improving UI, dashboards, pages, layouts, colors, typography, or accessibility.',
            aliases=('UI UX', 'design review', 'frontend design'),
        )
    )
