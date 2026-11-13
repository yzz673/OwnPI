from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_checkpoint_manager_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='checkpoint-manager',
            name='checkpoint-manager',
            description='Creates, verifies, and lists workflow checkpoints for tracking development progress via git snapshots.',
            tags=(),
        )
    )
