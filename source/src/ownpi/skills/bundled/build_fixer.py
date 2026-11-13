from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_build_fixer_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='build-fixer',
            name='build-fixer',
            description='Incrementally fixes Python type and lint errors using mypy, ruff, and pytest with a safe one-at-a-time approach.',
            tags=(),
        )
    )
