from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_doc_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='doc',
            name='doc',
            description=(
                'Use when the task involves reading, creating, or editing `.docx` documents, '
                'especially when formatting or layout fidelity matters; DOCX tooling is an '
                'on-demand capability and must be checked before use.'
            ),
            tags=(),
        )
    )
