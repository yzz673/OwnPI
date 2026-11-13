from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_doc_coauthoring_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='doc-coauthoring',
            name='doc-coauthoring',
            description='Co-author technical documents, proposals, decision records, and structured specs.',
            tags=('Writing', 'Documentation'),
            when_to_use='Use when drafting or revising technical docs, design notes, proposals, decision docs, or reusable written artifacts.',
            aliases=('documentation', 'technical writing', 'design doc'),
        )
    )
