from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_pdf_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='pdf',
            name='pdf',
            description=(
                'Provides guidance for extracting PDF content, rendering pages, creating '
                'paper-oriented PDFs, and checking PDF layout and visual quality.'
            ),
            tags=(),
        )
    )
