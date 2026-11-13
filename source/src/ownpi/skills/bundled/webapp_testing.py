from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_webapp_testing_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='webapp-testing',
            name='webapp-testing',
            description=(
                'Toolkit for interacting with and testing local web applications using '
                'Playwright. Supports verifying frontend functionality, debugging UI '
                'behavior, capturing browser screenshots, and viewing browser logs.'
            ),
            tags=(),
        )
    )
