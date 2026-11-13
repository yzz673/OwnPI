from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_uv_package_manager_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='uv-package-manager',
            name='uv-package-manager',
            description='Use uv for Python dependency management, virtual environments, lockfiles, and test commands.',
            tags=('Python', 'Tooling'),
            when_to_use='Use when setting up Python projects, managing dependencies, running uv commands, or fixing uv environment issues.',
            aliases=('uv', 'Python environment', 'dependency management'),
        )
    )
