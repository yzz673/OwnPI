from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ownpi.schemas.context import SkillSummary


@dataclass(frozen=True)
class ParsedSkillFile:
    metadata: dict[str, Any]
    body: str
    raw_text: str


@dataclass(frozen=True)
class SkippedSkill:
    path: str
    source: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SkillCatalogResult:
    skills: list[SkillSummary]
    skipped: list[SkippedSkill]


def parse_skill_file(path: Path) -> ParsedSkillFile:
    raw_text = path.read_text(encoding="utf-8").strip()
    metadata, body = _split_frontmatter(raw_text)
    return ParsedSkillFile(metadata={str(key): value for key, value in metadata.items()}, body=body, raw_text=raw_text)


def build_skill_catalog(skill_roots: Iterable[tuple[str, Path]]) -> list[SkillSummary]:
    return build_skill_catalog_result(skill_roots).skills


def build_skill_catalog_result(skill_roots: Iterable[tuple[str, Path]]) -> SkillCatalogResult:
    catalog: list[SkillSummary] = []
    skipped: list[SkippedSkill] = []
    seen: set[str] = set()
    for priority, (source, root) in enumerate(skill_roots):
        if not root.exists():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            try:
                parsed = parse_skill_file(skill_file)
            except (OSError, yaml.YAMLError) as exc:
                skipped.append(SkippedSkill(path=str(skill_file), source=source, reason=str(exc)))
                continue
            metadata = parsed.metadata
            body = parsed.body
            name = str(metadata.get("name") or _extract_heading(body) or skill_file.parent.name).strip()
            key = name.casefold()
            if key in seen:
                continue
            description = str(metadata.get("description") or _extract_first_paragraph(body) or "No description provided.").strip()
            tags = _normalize_tags(metadata.get("tags"))
            aliases = _normalize_tags(metadata.get("aliases"))
            catalog.append(
                SkillSummary(
                    name=name,
                    description=description,
                    path=str(skill_file),
                    source=source,
                    tags=tags,
                    priority=priority,
                    when_to_use=_coerce_optional_string(metadata.get("when_to_use", metadata.get("when-to-use"))),
                    aliases=aliases,
                )
            )
            seen.add(key)
    return SkillCatalogResult(skills=catalog, skipped=skipped)


def render_skill_catalog(catalog: Iterable[SkillSummary]) -> str:
    items = list(catalog)
    if not items:
        return "<available_skills>\nNo skills available.\n</available_skills>"
    lines = ["<available_skills>"]
    for item in items:
        tags_text = ", ".join(item.tags) if item.tags else ""
        lines.extend(
            [
                "  <skill>",
                f"    <name>{item.name}</name>",
                f"    <description>{item.description}</description>",
                f"    <path>{item.path}</path>",
                f"    <source>{item.source}</source>",
                f"    <tags>{tags_text}</tags>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def ordered_skill_names(*groups: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw_name in group:
            name = str(raw_name).strip()
            key = name.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(name)
    return tuple(ordered)


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        return {}, text
    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :]).strip()
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        return {}, body
    return parsed, body


def _extract_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _extract_first_paragraph(text: str) -> str | None:
    paragraph_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        paragraph_lines.append(stripped)
    if not paragraph_lines:
        return None
    return " ".join(paragraph_lines)


def _normalize_tags(raw_tags: object) -> tuple[str, ...]:
    if isinstance(raw_tags, list):
        return tuple(str(item).strip() for item in raw_tags if str(item).strip())
    if isinstance(raw_tags, str) and raw_tags.strip():
        return (raw_tags.strip(),)
    return ()


def _coerce_optional_string(value: object) -> str | None:
    text = str(value or '').strip()
    return text or None
