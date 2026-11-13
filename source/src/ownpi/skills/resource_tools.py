from __future__ import annotations

import errno
import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ownpi.app.paths import AppPaths
from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import (
    DEFAULT_SKILL_RESOURCE_PAGE_LINES,
    MAX_SKILL_LIST_ITEMS,
    MAX_SKILL_RESOURCE_PAGE_LINES,
)
from ownpi.schemas.context import SkillSummary
from ownpi.product.packs import (
    brainlib_module_resource,
    resolve_neuroscience_skill_root,
)
from ownpi.skills.bundled import bundled_skill_root, is_bundled_skill_path
from ownpi.skills.snapshot import SkillCatalogSnapshot
from ownpi.tools.common import clamp_int, metadata_for_context

if TYPE_CHECKING:
    from ownpi.runtime.tools.definitions import ToolExecutionContext


async def run_skills_list(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    catalog = _enabled_catalog(context)
    cursor = clamp_int(
        arguments.get('cursor'),
        default=0,
        minimum=0,
        maximum=max(len(catalog), 0),
    )
    limit = clamp_int(
        arguments.get('limit'),
        default=20,
        minimum=1,
        maximum=MAX_SKILL_LIST_ITEMS,
    )
    selected = catalog[cursor : cursor + limit]
    next_cursor = cursor + len(selected) if cursor + len(selected) < len(catalog) else None
    skills = [_catalog_entry(item) for item in selected]
    return ToolResult(
        ok=True,
        tool_name='skills_list',
        data={
            'skills': skills,
            'total': len(catalog),
            'cursor': cursor,
            'next_cursor': next_cursor,
        },
        stdout=(
            f'skills={len(skills)}\n'
            f'total={len(catalog)}\n'
            f'next_cursor={next_cursor}'
        ),
        metadata=metadata_for_context(
            context,
            result_count=len(skills),
            total_skill_count=len(catalog),
            next_cursor=next_cursor,
        ),
    )


async def run_skills_read(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> ToolResult:
    package = str(arguments.get('package') or '').strip()
    if not package:
        return ToolResult(
            ok=False,
            tool_name='skills_read',
            error='package is required',
        )
    resource = str(arguments.get('resource') or 'SKILL.md')
    legacy_module_resource = brainlib_module_resource(package)
    if legacy_module_resource is not None:
        module_root = PurePosixPath(legacy_module_resource).parent
        package = 'brainlib'
        resource = (
            legacy_module_resource
            if resource == 'SKILL.md'
            else str(module_root / resource)
        )
    summary = _resolve_skill(_enabled_catalog(context), package)
    if summary is None:
        return ToolResult(
            ok=False,
            tool_name='skills_read',
            error=f'skill package is not available: {package}',
        )
    try:
        resource_path = _resolve_skill_resource(context, summary, resource)
        skill_root = _skill_root(context, summary).expanduser().resolve()
        resource_kind = 'directory' if resource_path.is_dir() else 'file'
        if resource_kind == 'file' and not resource_path.is_file():
            return ToolResult(
                ok=False,
                tool_name='skills_read',
                error=f'skill resource does not exist: {resource}',
            )
    except (ValueError, OSError, RuntimeError) as exc:
        return ToolResult(ok=False, tool_name='skills_read', error=str(exc))
    try:
        text = (_directory_contents(resource_path, skill_root) if resource_kind == 'directory'
                else resource_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError) as exc:
        return ToolResult(
            ok=False,
            tool_name='skills_read',
            error=f'failed to read skill resource: {exc}',
        )

    lines = text.splitlines()
    cursor = clamp_int(
        arguments.get('cursor'),
        default=1,
        minimum=1,
        maximum=max(len(lines) + 1, 1),
    )
    limit = clamp_int(
        arguments.get('limit'),
        default=DEFAULT_SKILL_RESOURCE_PAGE_LINES,
        minimum=1,
        maximum=MAX_SKILL_RESOURCE_PAGE_LINES,
    )
    requested_column_offset = _parse_non_negative_int(arguments.get('column_offset'))
    column_offset = requested_column_offset
    start = min(cursor - 1, len(lines))
    end = min(start + limit, len(lines))
    page_lines = lines[start:end]
    if page_lines and column_offset:
        page_lines[0] = page_lines[0][column_offset:]
    contents = '\n'.join(page_lines)
    next_cursor = end + 1 if end < len(lines) else None
    return ToolResult(
        ok=True,
        tool_name='skills_read',
        data={
            'skill_name': summary.name,
            'package': summary.name,
            'resource': resource,
            'skill_root': str(skill_root),
            'resource_kind': resource_kind,
            'contents': contents,
            'start_line': start + 1,
            'end_line': end,
            'total_lines': len(lines),
            'next_cursor': next_cursor,
            'requested_column_offset': requested_column_offset,
            'column_offset': column_offset,
            'next_column_offset': None,
        },
        stdout=(
            f'package={summary.name}\n'
            f'resource={resource}\n'
            f'lines={start + 1}-{end}/{len(lines)}\n'
            f'next_cursor={next_cursor}'
        ),
        metadata=metadata_for_context(
            context,
            skill_name=summary.name,
            package=summary.name,
            resource=resource,
            start_line=start + 1,
            end_line=end,
            total_lines=len(lines),
            next_cursor=next_cursor,
            requested_column_offset=requested_column_offset,
            column_offset=column_offset,
            next_column_offset=None,
            invocation_type='implicit',
        ),
    )


def _directory_contents(directory: Path, root: Path) -> str:
    entries = []
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        try:
            resolved = child.resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            continue
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                continue
            raise
        if not resolved.is_relative_to(root):
            continue
        if resolved.is_file():
            kind = 'file'
        elif resolved.is_dir():
            kind = 'directory'
        else:
            continue
        entries.append(json.dumps({'kind': kind, 'resource': child.relative_to(root).as_posix()},
                                  ensure_ascii=True))
    return '\n'.join(entries)


def _enabled_catalog(context: ToolExecutionContext) -> list[SkillSummary]:
    snapshot = getattr(context, 'skill_catalog_snapshot', None)
    if not isinstance(snapshot, SkillCatalogSnapshot):
        raise TypeError('skill resource tools require a bound SkillCatalogSnapshot')
    return list(snapshot.skills)


def _resolve_skill(
    catalog: list[SkillSummary],
    requested: str,
) -> SkillSummary | None:
    normalized = requested.removeprefix('bundled://').removeprefix('/').casefold()
    for summary in catalog:
        if requested.casefold() == summary.name.casefold():
            return summary
    for summary in catalog:
        identifiers = {
            summary.name.casefold(),
            summary.path.casefold(),
            summary.path.removeprefix('bundled://').casefold(),
            *(alias.casefold() for alias in summary.aliases),
        }
        if requested.casefold() in identifiers or normalized in identifiers:
            return summary
    return None


def _parse_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _resolve_skill_resource(
    context: ToolExecutionContext,
    summary: SkillSummary,
    resource: str,
) -> Path:
    normalized = resource.replace('\\', '/')
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('skill resource must stay inside the selected package')
    root = _skill_root(context, summary).expanduser().resolve()
    candidate = root.joinpath(*relative.parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError('skill resource must stay inside the selected package') from exc
    return candidate


def _skill_root(context: ToolExecutionContext, summary: SkillSummary) -> Path:
    if summary.source == 'pack:neuroscience':
        app_paths = getattr(context, 'app_paths', None)
        if not isinstance(app_paths, AppPaths):
            app_paths = AppPaths.discover(repo_root=Path(context.project_root))
        root = resolve_neuroscience_skill_root(app_paths, summary.path)
        if root is None:
            raise ValueError(f'skill package is not available: {summary.name}')
        return root
    if is_bundled_skill_path(summary.path):
        return bundled_skill_root(summary.path)
    return Path(summary.path).expanduser().parent


def _catalog_entry(summary: SkillSummary) -> dict[str, Any]:
    return {
        'name': summary.name,
        'description': summary.description,
        'source': summary.source,
        'locator': summary.name,
        'aliases': list(summary.aliases),
    }


__all__ = ['run_skills_list', 'run_skills_read']
