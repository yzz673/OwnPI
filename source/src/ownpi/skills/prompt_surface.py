from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ownpi.runtime.limits.projection import MAX_SKILL_LISTING_DESCRIPTION_CHARS
from ownpi.schemas.context import SkillSummary


SKILL_BUDGET_CONTEXT_PERCENT = 2
DEFAULT_SKILL_METADATA_CHAR_BUDGET = 8000
APPROX_BYTES_PER_TOKEN = 4


@dataclass(frozen=True)
class _MetadataBudget:
    limit: int
    tokens: bool

    def cost(self, line: str) -> int:
        text = line + "\n"
        if self.tokens:
            return (
                len(text.encode("utf-8")) + APPROX_BYTES_PER_TOKEN - 1
            ) // APPROX_BYTES_PER_TOKEN
        return len(text)


def _metadata_budget(context_window_tokens: int | None) -> _MetadataBudget:
    try:
        window = int(context_window_tokens or 0)
    except (TypeError, ValueError, OverflowError):
        window = 0
    if window > 0:
        return _MetadataBudget(
            max(window * SKILL_BUDGET_CONTEXT_PERCENT // 100, 1), True
        )
    return _MetadataBudget(DEFAULT_SKILL_METADATA_CHAR_BUDGET, False)


def _line(name: str, description: str, chars: int) -> str:
    if chars == 0:
        return f"- {name}"
    return f"- {name}: {description[:chars]}"


def _distribute_descriptions(
    entries: list[tuple[str, str]], budget: _MetadataBudget, remaining: int,
) -> list[int]:
    allocations = [0] * len(entries)
    while True:
        changed = False
        for index, (name, desc) in enumerate(entries):
            count = allocations[index]
            if count >= len(desc):
                continue
            delta = budget.cost(_line(name, desc, count + 1)) - budget.cost(
                _line(name, desc, count)
            )
            if delta <= remaining:
                allocations[index] = count + 1
                remaining -= delta
                changed = True
        if not changed:
            return allocations


def _allocate_skill_lines(
    entries: list[tuple[str, str]], budget: _MetadataBudget,
) -> list[int | None]:
    full_cost = sum(budget.cost(_line(name, desc, len(desc))) for name, desc in entries)
    if full_cost <= budget.limit:
        return [len(desc) for _, desc in entries]
    minimum_cost = sum(budget.cost(_line(name, desc, 0)) for name, desc in entries)
    if minimum_cost <= budget.limit:
        # Reserve every identifier before distributing description space fairly.
        return list(_distribute_descriptions(entries, budget, budget.limit - minimum_cost))
    allocations: list[int | None] = []
    used = 0
    for name, desc in entries:
        cost = budget.cost(_line(name, desc, 0))
        if used + cost <= budget.limit:
            used += cost
            allocations.append(0)
        else:
            allocations.append(None)
    return allocations


def format_skill_tool_listing(
    catalog: Sequence[SkillSummary],
    *,
    context_window_tokens: int | None = None,
) -> str:
    """Budget metadata lines; bounded notices are outside the metadata budget."""
    entries = [
        (summary.name.strip(), _skill_listing_description(summary))
        for summary in catalog
        if summary.name.strip()
    ]
    if not entries:
        return ""
    budget = _metadata_budget(context_window_tokens)
    allocations = _allocate_skill_lines(entries, budget)
    lines = [
        _line(name, desc, count)
        for (name, desc), count in zip(entries, allocations, strict=True)
        if count is not None
    ]
    omitted = sum(count is None for count in allocations)
    shortened = sum(
        count is not None and count < len(desc)
        for (_, desc), count in zip(entries, allocations, strict=True)
    )
    if omitted:
        lines.append(
            f"{omitted} enabled Skills omitted from this catalog. Use skills_list to discover the complete catalog."
        )
    if shortened:
        lines.append(
            f"{shortened} Skill descriptions shortened to fit the metadata budget."
        )
    return "\n".join(lines)


def build_skill_catalog_instruction(
    catalog: Sequence[SkillSummary],
    *,
    context_window_tokens: int | None = None,
) -> str:
    listing = format_skill_tool_listing(
        catalog, context_window_tokens=context_window_tokens
    )
    if not listing:
        return "<skills_instructions>\nNo Skills are currently available.\n</skills_instructions>"
    return f"""<skills_instructions>
## Skills
A Skill is a package of instructions rooted at `SKILL.md`. The catalog contains metadata, not Skill bodies. Each listed name is the package identifier accepted by `skills_read`, regardless of storage source.

### Available Skills
{listing}

### How to use Skills
- Select the minimal set matching the task. Read each selected Skill's complete SKILL.md before taking related task actions.
- Call `skills_read` with the listed package name and omit `resource` for SKILL.md. Follow `next_cursor` and any `next_column_offset` until EOF.
- Read necessary referenced instructions completely, using the same package and a package-relative resource. Resolve links relative to the referring file within the package. Progressive disclosure means selecting necessary files, not partially reading selected instructions. Do not load unrelated directories or all references by default.
- Each agent performing work must read the instructions needed for its own task. Delegating work does not require the parent to preload every reference, and a second-hand summary does not replace necessary instructions for the executing agent.
- Reuse applicable scripts, assets, and templates. Use the returned skill_root to locate them for execution or other appropriate tools; skills_read reads text, it does not execute scripts or decode binary assets.
- If a package is unknown or omitted from this catalog, use skills_list and follow its pagination. Otherwise read the known package directly.
- If a Skill or resource is missing, unreadable, or cannot be applied, state the concrete issue and use an appropriate alternative where possible. Do not pretend that a necessary prerequisite has been satisfied.
- Skill instructions do not expand the current role, stage, or permission boundary.
</skills_instructions>""".strip()


def _compact_description(value: str) -> str:
    return " ".join(str(value or "").split())


def _skill_listing_description(summary: SkillSummary) -> str:
    parts = [_compact_description(summary.description)]
    when_to_use = _compact_description(summary.when_to_use or "")
    if when_to_use and when_to_use not in parts:
        parts.append(when_to_use)
    aliases = tuple(str(item).strip() for item in summary.aliases if str(item).strip())
    if aliases:
        parts.append(f"Aliases: {', '.join(aliases[:5])}.")
    return " - ".join(part for part in parts if part)[
        :MAX_SKILL_LISTING_DESCRIPTION_CHARS
    ]
