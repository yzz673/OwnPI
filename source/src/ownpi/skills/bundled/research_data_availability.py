from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_research_data_availability_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='research-data-availability',
            name='research-data-availability',
            description='Prepare publication-ready Data Availability statements, repository plans, and FAIR metadata checks.',
            tags=('Writing', 'Research Data', 'Publication'),
            when_to_use='Use when drafting or auditing manuscript data availability, dataset citations, repositories, restricted data, or FAIR metadata.',
            aliases=('data availability', 'FAIR metadata', 'dataset citation'),
        )
    )
