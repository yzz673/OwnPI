from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from ownpi.app.paths import AppPaths
from ownpi.chat.command_registry import CommandDefinition, CommandRegistry, CommandResult
from ownpi.schemas.context import SkillSummary
from ownpi.schemas.runtime import ExtensionTrustConfig, HookRegistryConfig
from ownpi.skills.catalog import build_skill_catalog_result


CUSTOM_COMMAND_SOURCE_PREFIX = 'custom_command:'


@dataclass(frozen=True)
class ExtensionRoot:
    source: str
    root: Path
    priority: int


@dataclass(frozen=True)
class SkippedExtension:
    path: str
    source: str
    reason: str
    kind: str = 'extension'

    def to_dict(self) -> dict[str, str]:
        return {
            'path': self.path,
            'source': self.source,
            'reason': self.reason,
            'kind': self.kind,
        }


@dataclass(frozen=True)
class ExtensionCommandRegistration:
    command_name: str
    source: str
    registered: bool
    reason: str = ''


@dataclass(frozen=True)
class ExtensionSourceStatus:
    source: str
    root: str
    trusted: bool
    kind: str = 'extension'

    def to_dict(self) -> dict[str, Any]:
        return {
            'source': self.source,
            'root': self.root,
            'trusted': self.trusted,
            'kind': self.kind,
        }


@dataclass(frozen=True)
class HookPackageStatus:
    source: str
    path: str
    trusted: bool
    enabled: bool
    event_count: int = 0
    hook_count: int = 0
    content_hash: str = ''
    trust_status: str = 'untrusted'

    def to_dict(self) -> dict[str, Any]:
        return {
            'source': self.source,
            'path': self.path,
            'trusted': self.trusted,
            'enabled': self.enabled,
            'event_count': self.event_count,
            'hook_count': self.hook_count,
            'content_hash': self.content_hash,
            'trust_status': self.trust_status,
        }


@dataclass(frozen=True)
class ExtensionSnapshot:
    skill_roots: tuple[tuple[str, Path], ...] = ()
    skills: tuple[SkillSummary, ...] = ()
    command_definitions: tuple[CommandDefinition, ...] = ()
    hooks_config: HookRegistryConfig = field(default_factory=HookRegistryConfig)
    sources: tuple[ExtensionSourceStatus, ...] = ()
    hook_packages: tuple[HookPackageStatus, ...] = ()
    skipped: tuple[SkippedExtension, ...] = ()

    def visible_skill_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.skills)


def discover_extensions(
    app_paths: AppPaths,
    *,
    project_root: Path | None = None,
    trust_config: ExtensionTrustConfig | None = None,
) -> ExtensionSnapshot:
    trust = trust_config or ExtensionTrustConfig()
    roots = _extension_roots(app_paths, project_root=project_root)
    skill_roots: list[tuple[str, Path]] = []
    command_definitions: list[CommandDefinition] = []
    hook_configs: list[HookRegistryConfig] = []
    sources: list[ExtensionSourceStatus] = []
    hook_packages: list[HookPackageStatus] = []
    skipped: list[SkippedExtension] = []
    for root in roots:
        sources.append(
            ExtensionSourceStatus(
                source=root.source,
                root=str(root.root),
                trusted=_extension_source_is_trusted(root.source, trust, effect='hooks'),
            )
        )
        skills_dir = root.root / 'skills'
        if skills_dir.exists() and root.source != 'app_home':
            skill_roots.append((_skill_source_id(root.source), skills_dir))
        commands_dir = root.root / 'commands'
        if commands_dir.exists():
            commands, command_skipped = load_custom_command_definitions(
                commands_dir,
                source=root.source,
                trust_config=trust,
            )
            command_definitions.extend(commands)
            skipped.extend(command_skipped)
        hooks_dir = root.root / 'hooks'
        if hooks_dir.exists():
            config, hook_skipped = load_hook_package_config(hooks_dir, source=root.source)
            trusted = _hook_package_is_trusted(root.source, config, trust)
            hook_packages.append(
                _hook_package_status(
                    hooks_dir,
                    source=root.source,
                    config=config,
                    trust_config=trust,
                )
            )
            if trusted:
                hook_configs.append(config)
            elif config.enabled:
                skipped.append(
                    SkippedExtension(
                        path=str(hooks_dir / 'hooks.json'),
                        source=root.source,
                        reason='extension hook source is not trusted',
                        kind='hook',
                    )
                )
            skipped.extend(hook_skipped)
        plugins_dir = root.root / 'plugins'
        if plugins_dir.exists():
            for plugin_root in sorted(item for item in plugins_dir.iterdir() if item.is_dir()):
                plugin_source = f'plugin:{root.source}:{plugin_root.name}'
                sources.append(
                    ExtensionSourceStatus(
                        source=plugin_source,
                        root=str(plugin_root),
                        trusted=_extension_source_is_trusted(plugin_source, trust, effect='hooks'),
                        kind='plugin',
                    )
                )
                plugin_skipped = _validate_plugin_root(plugin_root, source=plugin_source)
                if plugin_skipped is not None:
                    skipped.append(plugin_skipped)
                    continue
                plugin_skills = plugin_root / 'skills'
                if plugin_skills.exists():
                    skill_roots.append((plugin_source, plugin_skills))
                plugin_commands = plugin_root / 'commands'
                if plugin_commands.exists():
                    commands, command_skipped = load_custom_command_definitions(
                        plugin_commands,
                        source=plugin_source,
                        trust_config=trust,
                    )
                    command_definitions.extend(commands)
                    skipped.extend(command_skipped)
                plugin_hooks = plugin_root / 'hooks'
                if plugin_hooks.exists():
                    config, hook_skipped = load_hook_package_config(
                        plugin_hooks,
                        source=plugin_source,
                    )
                    trusted = _hook_package_is_trusted(plugin_source, config, trust)
                    hook_packages.append(
                        _hook_package_status(
                            plugin_hooks,
                            source=plugin_source,
                            config=config,
                            trust_config=trust,
                        )
                    )
                    if trusted:
                        hook_configs.append(config)
                    elif config.enabled:
                        skipped.append(
                            SkippedExtension(
                                path=str(plugin_hooks / 'hooks.json'),
                                source=plugin_source,
                                reason='extension hook source is not trusted',
                                kind='hook',
                            )
                        )
                    skipped.extend(hook_skipped)
    catalog_result = build_skill_catalog_result(skill_roots)
    skipped.extend(
        SkippedExtension(path=item.path, source=item.source, reason=item.reason, kind='skill')
        for item in catalog_result.skipped
    )
    return ExtensionSnapshot(
        skill_roots=tuple(skill_roots),
        skills=tuple(catalog_result.skills),
        command_definitions=tuple(command_definitions),
        hooks_config=HookRegistryConfig.merge(*hook_configs),
        sources=tuple(sources),
        hook_packages=tuple(hook_packages),
        skipped=tuple(skipped),
    )


def sync_custom_commands(
    registry: CommandRegistry,
    definitions: tuple[CommandDefinition, ...] | list[CommandDefinition],
) -> tuple[ExtensionCommandRegistration, ...]:
    registry.unregister_by_source_prefix(CUSTOM_COMMAND_SOURCE_PREFIX)
    registrations: list[ExtensionCommandRegistration] = []
    for definition in definitions:
        try:
            registry.register(definition)
        except Exception as exc:
            registrations.append(
                ExtensionCommandRegistration(
                    command_name=definition.name,
                    source=definition.source,
                    registered=False,
                    reason=str(exc),
                )
            )
            continue
        registrations.append(
            ExtensionCommandRegistration(
                command_name=definition.name,
                source=definition.source,
                registered=True,
            )
        )
    return tuple(registrations)


def load_custom_command_definitions(
    commands_dir: Path,
    *,
    source: str,
    trust_config: ExtensionTrustConfig | None = None,
) -> tuple[tuple[CommandDefinition, ...], tuple[SkippedExtension, ...]]:
    definitions: list[CommandDefinition] = []
    skipped: list[SkippedExtension] = []
    for path in sorted(commands_dir.rglob('*.md')):
        if path.name.casefold() == 'skill.md':
            continue
        try:
            definitions.append(
                create_markdown_command(
                    path,
                    commands_dir=commands_dir,
                    source=source,
                    trust_config=trust_config,
                )
            )
        except Exception as exc:
            skipped.append(SkippedExtension(path=str(path), source=source, reason=str(exc), kind='command'))
    return tuple(definitions), tuple(skipped)


def create_markdown_command(
    path: Path,
    *,
    commands_dir: Path,
    source: str,
    trust_config: ExtensionTrustConfig | None = None,
) -> CommandDefinition:
    raw_text = path.read_text(encoding='utf-8').strip()
    metadata, body = _split_frontmatter(raw_text)
    command_name = _command_name(path, commands_dir=commands_dir, metadata=metadata)
    description = str(metadata.get('description') or _extract_first_paragraph(body) or 'Invoke custom command.').strip()
    argument_hint = _optional_string(metadata.get('argument_hint', metadata.get('argument-hint')))
    allowed_tools = tuple(_coerce_string_list(metadata.get('allowed_tools', metadata.get('allowed-tools'))))
    model = _optional_string(metadata.get('model'))
    reasoning_effort = _optional_string(metadata.get('effort', metadata.get('reasoning_effort')))
    hooks_config = _coerce_hooks_config(metadata.get('hooks'))
    user_invocable = _coerce_bool(metadata.get('user_invocable', metadata.get('user-invocable')), default=True)
    prompt_body = body.strip() or raw_text
    trust_policy = _extension_trust_policy(
        source,
        trust_config or ExtensionTrustConfig(),
        has_allowed_tools=bool(allowed_tools),
        has_hooks=bool(hooks_config),
        has_model=bool(model),
        has_reasoning_effort=bool(reasoning_effort),
    )
    extension_invocation = _extension_invocation_metadata(source, kind='command')

    def _execute(_context: Any, message: str) -> CommandResult:
        arguments = _command_arguments(message)
        prompt = _render_custom_command_prompt(
            command_name=command_name,
            path=path,
            source=source,
            body=prompt_body,
            arguments=arguments,
        )
        return CommandResult(
            handled=True,
            prompt=prompt,
            metadata={
                'source': 'custom_command',
                'command_name': command_name,
                'command_path': str(path),
                'command_source': source,
                'allowed_tools': list(allowed_tools),
                'model': model,
                'reasoning_effort': reasoning_effort,
                'hooks_config': dict(hooks_config or {}),
                'argument_hint': argument_hint,
                'user_invocable': user_invocable,
                'extension_invocation': extension_invocation,
                'trust_policy': trust_policy,
            },
        )

    return CommandDefinition(
        name=command_name,
        description=description,
        source=f'{CUSTOM_COMMAND_SOURCE_PREFIX}{source}:{path.relative_to(commands_dir).as_posix()}',
        kind='prompt',
        allowed_tools=allowed_tools,
        model=model,
        reasoning_effort=reasoning_effort,
        argument_hint=argument_hint,
        user_invocable=user_invocable,
        hook_names=tuple(str(key) for key in hooks_config.keys()) if isinstance(hooks_config, dict) else (),
        metadata={
            'command_path': str(path),
            'command_source': source,
            'hooks_config': dict(hooks_config or {}),
            'extension_invocation': extension_invocation,
            'trust_policy': trust_policy,
        },
        execute=_execute,
    )


def _extension_invocation_metadata(source: str, *, kind: str) -> dict[str, str | None]:
    parts = source.split(':')
    source_origin = source
    plugin_name: str | None = None
    if source == 'app_home':
        source_origin = 'app_home'
    elif source == 'project':
        source_origin = 'project'
    elif len(parts) == 3 and parts[0] == 'plugin':
        source_origin = parts[1]
        plugin_name = parts[2]
    return {
        'source_id': source,
        'source_origin': source_origin,
        'source_kind': kind,
        'plugin_name': plugin_name,
        'trust_level': _extension_trust_level(source),
    }


def _skill_source_id(source: str) -> str:
    if source == 'project':
        return 'extension:project'
    return source


def _extension_trust_policy(
    source: str,
    trust_config: ExtensionTrustConfig,
    *,
    has_allowed_tools: bool,
    has_hooks: bool,
    has_model: bool = False,
    has_reasoning_effort: bool = False,
) -> dict[str, Any]:
    restricted_effects: list[str] = []
    if has_allowed_tools and not _extension_source_is_trusted(source, trust_config, effect='allowed_tools'):
        restricted_effects.append('allowed_tools')
    if has_hooks and not _extension_source_is_trusted(source, trust_config, effect='hooks'):
        restricted_effects.append('hooks')
    if has_model and not _extension_source_is_trusted(source, trust_config, effect='model'):
        restricted_effects.append('model')
    if has_reasoning_effort and not _extension_source_is_trusted(source, trust_config, effect='reasoning_effort'):
        restricted_effects.append('reasoning_effort')
    warnings = [
        f'custom command runtime effect restricted by extension trust policy: {effect}'
        for effect in restricted_effects
    ]
    return {
        'trust_level': _extension_trust_level(source),
        'source_id': source,
        'warnings': warnings,
        'restricted_effects': restricted_effects,
    }


def _extension_trust_level(source: str) -> str:
    if source == 'app_home':
        return 'app_home'
    if source == 'project':
        return 'project'
    if source.startswith('plugin:'):
        return 'plugin'
    return 'unknown'


def _extension_source_is_trusted(
    source: str,
    trust_config: ExtensionTrustConfig,
    *,
    effect: str,
) -> bool:
    if source == 'app_home':
        return True
    parts = source.split(':')
    trusted_sources = {str(item).strip().casefold() for item in trust_config.trusted_sources if str(item).strip()}
    trusted_plugins = {str(item).strip().casefold() for item in trust_config.trusted_plugins if str(item).strip()}
    source_key = source.casefold()
    if source_key in trusted_sources:
        return True
    if source == 'project':
        return effect == 'hooks' and bool(trust_config.allow_project_hooks) or 'project' in trusted_sources
    if len(parts) == 3 and parts[0] == 'plugin':
        origin = parts[1].casefold()
        if origin == 'app_home':
            return True
        if source_key in trusted_plugins or source_key in trusted_sources:
            return True
        return origin == 'project' and 'project' in trusted_sources
    return False


def load_hook_package_config(
    hooks_dir: Path,
    *,
    source: str,
    env: dict[str, str] | None = None,
) -> tuple[HookRegistryConfig, tuple[SkippedExtension, ...]]:
    hooks_file = hooks_dir / 'hooks.json'
    if not hooks_file.exists():
        return HookRegistryConfig(), ()
    try:
        payload = yaml.safe_load(hooks_file.read_text(encoding='utf-8')) or {}
        if not isinstance(payload, dict):
            raise ValueError('hooks.json must contain an object')
        raw_hooks = payload.get('hooks')
        if not isinstance(raw_hooks, dict):
            raise ValueError('hooks.json must contain a hooks object')
        events = _normalize_hook_events(raw_hooks, hooks_dir=hooks_dir, env=env)
        return HookRegistryConfig.from_events_dict(events, source=source), ()
    except Exception as exc:
        return HookRegistryConfig(), (
            SkippedExtension(path=str(hooks_file), source=source, reason=str(exc), kind='hook'),
        )


def _hook_package_status(
    hooks_dir: Path,
    *,
    source: str,
    config: HookRegistryConfig,
    trust_config: ExtensionTrustConfig,
) -> HookPackageStatus:
    trust_status = _hook_package_trust_status(source, config, trust_config)
    return HookPackageStatus(
        source=source,
        path=str(hooks_dir / 'hooks.json'),
        trusted=trust_status in {'managed', 'trusted'},
        enabled=config.enabled and trust_status in {'managed', 'trusted'},
        event_count=len(config.events),
        hook_count=sum(len(items) for items in config.events.values()),
        content_hash=_hook_package_content_hash(config),
        trust_status=trust_status,
    )


def _hook_package_is_trusted(
    source: str,
    config: HookRegistryConfig,
    trust_config: ExtensionTrustConfig,
) -> bool:
    if source == 'app_home' or source.startswith('plugin:app_home:'):
        return True
    if not _extension_source_is_trusted(source, trust_config, effect='hooks'):
        return False
    expected = str(trust_config.trusted_hook_hashes.get(source, '') or '').strip()
    return bool(expected and expected == _hook_package_content_hash(config))


def _hook_package_trust_status(
    source: str,
    config: HookRegistryConfig,
    trust_config: ExtensionTrustConfig,
) -> str:
    if source == 'app_home' or source.startswith('plugin:app_home:'):
        return 'managed'
    if not _extension_source_is_trusted(source, trust_config, effect='hooks'):
        return 'untrusted'
    expected = str(trust_config.trusted_hook_hashes.get(source, '') or '').strip()
    if not expected:
        return 'untrusted'
    return 'trusted' if expected == _hook_package_content_hash(config) else 'modified'


def _hook_package_content_hash(config: HookRegistryConfig) -> str:
    payload = json.dumps(config.to_dict(), ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _extension_roots(app_paths: AppPaths, *, project_root: Path | None) -> tuple[ExtensionRoot, ...]:
    roots = [ExtensionRoot(source='app_home', root=app_paths.app_home, priority=0)]
    if project_root is not None:
        project_extension_root = Path(project_root).expanduser().resolve() / '.ownpi'
        if project_extension_root.exists():
            roots.append(ExtensionRoot(source='project', root=project_extension_root, priority=1))
    return tuple(roots)


def _validate_plugin_root(plugin_root: Path, *, source: str) -> SkippedExtension | None:
    manifest = plugin_root / 'plugin.json'
    if not manifest.exists():
        return SkippedExtension(path=str(plugin_root), source=source, reason='missing plugin.json', kind='plugin')
    try:
        payload = yaml.safe_load(manifest.read_text(encoding='utf-8')) or {}
        if not isinstance(payload, dict):
            raise ValueError('plugin.json must contain an object')
        name = str(payload.get('name') or '').strip()
        if not name:
            raise ValueError('plugin.json requires name')
    except Exception as exc:
        return SkippedExtension(path=str(manifest), source=source, reason=str(exc), kind='plugin')
    return None


def _normalize_hook_events(
    raw_hooks: dict[str, Any],
    *,
    hooks_dir: Path,
    env: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = {}
    for event_name, raw_matchers in raw_hooks.items():
        specs: list[dict[str, Any]] = []
        matcher_items = raw_matchers if isinstance(raw_matchers, list) else [raw_matchers]
        for raw_matcher in matcher_items:
            if not isinstance(raw_matcher, dict):
                continue
            matcher = str(raw_matcher.get('matcher', raw_matcher.get('matches', '')) or '')
            raw_hook_items = raw_matcher.get('hooks', raw_matcher.get('command'))
            if isinstance(raw_hook_items, dict):
                hook_items = [raw_hook_items]
            elif isinstance(raw_hook_items, list) and raw_hook_items and all(isinstance(item, dict) for item in raw_hook_items):
                hook_items = list(raw_hook_items)
            elif raw_hook_items is not None:
                hook_items = [{'command': raw_hook_items}]
            else:
                hook_items = []
            for item in hook_items:
                command = _coerce_command_list(item.get('command'))
                if not command:
                    continue
                specs.append(
                    {
                        'name': item.get('name', raw_matcher.get('name', '')),
                        'command': command,
                        'timeout_seconds': item.get('timeout_seconds', item.get('timeout', raw_matcher.get('timeout_seconds', 10))),
                        'cwd': str(hooks_dir.parent),
                        'env': {**dict(env or {}), **(item.get('env', {}) if isinstance(item.get('env'), dict) else {})},
                        'inherit_env': item.get('inherit_env', False),
                        'matcher': str(item.get('matcher', matcher) or ''),
                    }
                )
        if specs:
            events[str(event_name)] = specs
    return events


def _coerce_command_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith('---\n'):
        return {}, text
    lines = text.splitlines()
    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            closing_index = index
            break
    if closing_index is None:
        return {}, text
    frontmatter = '\n'.join(lines[1:closing_index])
    body = '\n'.join(lines[closing_index + 1 :]).strip()
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        return {}, body
    return {str(key): value for key, value in parsed.items()}, body


def _command_name(path: Path, *, commands_dir: Path, metadata: dict[str, Any]) -> str:
    explicit = _optional_string(metadata.get('name'))
    if explicit:
        return explicit if explicit.startswith('/') else f'/{explicit}'
    relative = path.relative_to(commands_dir).with_suffix('')
    parts = [_slug_part(part) for part in relative.parts]
    return '/' + ':'.join(part for part in parts if part)


def _slug_part(value: str) -> str:
    import re

    return re.sub(r'[^a-z0-9_-]+', '-', value.casefold()).strip('-') or 'command'


def _extract_first_paragraph(text: str) -> str | None:
    paragraph: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith('#'):
            continue
        paragraph.append(stripped)
    return ' '.join(paragraph) if paragraph else None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() not in {'0', 'false', 'no', 'off'}
    return bool(value)


def _coerce_hooks_config(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    return {str(key): item for key, item in value.items()}


def _command_arguments(message: str) -> str:
    import shlex

    try:
        tokens = shlex.split(message)
    except ValueError:
        tokens = str(message or '').split()
    if len(tokens) <= 1:
        return ''
    return str(message or '')[len(tokens[0]) :].strip()


def _render_custom_command_prompt(
    *,
    command_name: str,
    path: Path,
    source: str,
    body: str,
    arguments: str,
) -> str:
    argument_text = arguments.strip()
    if not argument_text:
        argument_text = f'Run {command_name}.'
    return '\n'.join(
        [
            '<command_invocation>',
            f'name: {command_name}',
            f'source: {source}',
            f'path: {path}',
            f'arguments: {argument_text}',
            '</command_invocation>',
            '',
            '<command_instructions>',
            body,
            '</command_instructions>',
            '',
            '<user_request>',
            argument_text,
            '</user_request>',
        ]
    )
