from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from ownpi.schemas.context import SkillSummary

BUNDLED_SKILL_URI_PREFIX = 'bundled://'
_BUNDLED_CONTENT_PACKAGE = 'ownpi.skills.bundled.content'


@dataclass(frozen=True)
class BundledSkillDefinition:
    slug: str
    name: str
    description: str
    prompt_body: str = ''
    tags: tuple[str, ...] = ('Research',)
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    effort: str | None = None
    when_to_use: str | None = None
    aliases: tuple[str, ...] = ()
    disable_model_invocation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return f'{BUNDLED_SKILL_URI_PREFIX}{self.slug}'

    @property
    def command_name(self) -> str:
        return f'/{self.slug}'

    @property
    def skill_root(self) -> str:
        return str(bundled_skill_root(self.slug))

    @property
    def skill_file(self) -> str:
        return str(bundled_skill_file(self.slug))

    def to_summary(self, *, priority: int = 1000) -> SkillSummary:
        return SkillSummary(
            name=self.name,
            description=self.description,
            path=self.path,
            source='bundled',
            tags=self.tags,
            priority=priority,
            when_to_use=self.when_to_use,
            aliases=self.aliases,
        )


_BUNDLED_SKILLS: dict[str, BundledSkillDefinition] = {}
_INITIALIZED = False


def register_bundled_skill(definition: BundledSkillDefinition) -> None:
    slug = _normalize_bundled_slug(definition.slug)
    if not slug:
        raise ValueError('Bundled skill slug cannot be empty.')
    raw_text, metadata = _read_bundled_skill_file(slug)
    name = str(metadata.get('name') or definition.name or slug).strip() or slug
    description = str(metadata.get('description') or definition.description or 'Invoke bundled skill.').strip()
    tags = _coerce_string_tuple(metadata.get('tags')) or definition.tags
    aliases = _coerce_string_tuple(metadata.get('aliases')) or definition.aliases
    normalized = BundledSkillDefinition(
        slug=slug,
        name=name,
        description=description,
        prompt_body=raw_text,
        tags=tags,
        allowed_tools=definition.allowed_tools,
        model=definition.model,
        effort=definition.effort,
        when_to_use=_coerce_optional_string(metadata.get('when_to_use', metadata.get('when-to-use'))) or definition.when_to_use,
        aliases=aliases,
        disable_model_invocation=definition.disable_model_invocation,
        metadata={
            'skill_root': str(bundled_skill_root(slug)),
            'skill_file': str(bundled_skill_file(slug)),
            **dict(definition.metadata),
        },
    )
    existing = _BUNDLED_SKILLS.get(slug)
    if existing is not None and existing != normalized:
        raise ValueError(f'Bundled skill already registered with different definition: {slug}')
    _BUNDLED_SKILLS[slug] = normalized


def init_bundled_skills() -> tuple[BundledSkillDefinition, ...]:
    global _INITIALIZED
    if not _INITIALIZED:
        from ownpi.skills.bundled.index import register_all_bundled_skills

        register_all_bundled_skills()
        _INITIALIZED = True
    return tuple(_BUNDLED_SKILLS.values())


def init_bundled_research_skills() -> tuple[BundledSkillDefinition, ...]:
    return tuple(
        definition
        for definition in init_bundled_skills()
        if 'Research' in definition.tags
    )


def get_bundled_skill_definitions() -> tuple[BundledSkillDefinition, ...]:
    return init_bundled_skills()


def get_bundled_skill_summaries() -> list[SkillSummary]:
    return [
        definition.to_summary(priority=index)
        for index, definition in enumerate(get_bundled_skill_definitions(), start=1000)
    ]


def get_bundled_skill_definition(slug_or_uri: str) -> BundledSkillDefinition | None:
    requested = _normalize_bundled_slug(slug_or_uri)
    if not requested:
        return None
    for definition in get_bundled_skill_definitions():
        if definition.slug == requested or definition.name.casefold() == requested.casefold():
            return definition
    return None


def get_bundled_skill_body(slug_or_uri: str) -> str:
    definition = get_bundled_skill_definition(slug_or_uri)
    if definition is None:
        raise KeyError(f'Bundled skill not found: {slug_or_uri}')
    return definition.prompt_body.strip()


def is_bundled_skill_path(path: str) -> bool:
    return str(path or '').startswith(BUNDLED_SKILL_URI_PREFIX)


def bundled_skill_root(slug: str) -> Path:
    return Path(resources.files(_BUNDLED_CONTENT_PACKAGE).joinpath(_normalize_bundled_slug(slug)))


def bundled_skill_file(slug: str) -> Path:
    return bundled_skill_root(slug) / 'SKILL.md'


def _normalize_bundled_slug(value: str) -> str:
    text = str(value or '').strip()
    if text.startswith(BUNDLED_SKILL_URI_PREFIX):
        text = text.removeprefix(BUNDLED_SKILL_URI_PREFIX)
    if text.startswith('/'):
        text = text.removeprefix('/')
    return text.strip().casefold()


def _read_bundled_skill_file(slug: str) -> tuple[str, dict[str, Any]]:
    raw_text = bundled_skill_file(slug).read_text(encoding='utf-8').strip()
    metadata: dict[str, Any] = {}
    if raw_text.startswith('---\n'):
        end = raw_text.find('\n---', 4)
        if end != -1:
            frontmatter_text = raw_text[4:end]
            loaded = yaml.safe_load(frontmatter_text) or {}
            if isinstance(loaded, dict):
                metadata = {str(key): value for key, value in loaded.items()}
    return raw_text, metadata


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()
