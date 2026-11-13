from __future__ import annotations

from ownpi.skills.bundled.registry import (
    BundledSkillDefinition,
    register_bundled_skill,
)


def register_planning_with_files_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug="planning-with-files",
            name="planning-with-files",
            description=(
                "This skill should be used for complex or long-running work that needs a "
                "durable Markdown planning blueprint, using one file or a macro-to-micro "
                "file tree as needed."
            ),
            tags=("Planning",),
            when_to_use=(
                "Use when work spans many actions and its plan must stay current; skip it "
                "for simple one-step work."
            ),
            aliases=(
                "durable planning",
                "markdown planning",
                "planning blueprint",
            ),
        )
    )
