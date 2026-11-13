from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_verification_loop_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='verification-loop',
            name='verification-loop',
            description='Choose and run meaningful verification before claiming a change is ready.',
            tags=('Verification', 'Coding'),
            when_to_use='Use after code, experiment, or research-implementation changes when verification is needed.',
            aliases=('verify', 'quality gate', 'test plan'),
        )
    )
