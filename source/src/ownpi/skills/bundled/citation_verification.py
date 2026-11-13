from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_citation_verification_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='citation-verification',
            name='citation-verification',
            description=(
                'Verify scholarly source identity, provenance-bearing BibTeX, '
                'citation receipts, and bibliography identity closure.'
            ),
            tags=('Research', 'Writing'),
            when_to_use=(
                'Use when resolving citation metadata or BibTeX, preventing fabricated '
                'references, or validating a manuscript bibliography.'
            ),
            aliases=('citation check', 'reference verification', 'source audit'),
        )
    )
