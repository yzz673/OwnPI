from __future__ import annotations

from collections.abc import Iterable

from ownpi.runtime.tools.definitions import ToolDefinition


def validate_tool_definition_snapshot(
    definitions: Iterable[ToolDefinition],
    *,
    registry_label: str = 'tool registry',
) -> None:
    identifiers: dict[str, tuple[str, str]] = {}
    schema_variants: dict[str, str] = {}
    for definition in definitions:
        summary = definition.to_summary()
        canonical_name = str(summary.name or '').strip()
        if not canonical_name:
            raise ValueError(f'{registry_label} definition has an empty canonical name')
        _claim_identifier(
            identifiers=identifiers,
            value=canonical_name,
            identifier_kind='canonical name',
            canonical_name=canonical_name,
            registry_label=registry_label,
        )
        for alias in summary.aliases:
            _claim_identifier(
                identifiers=identifiers,
                value=alias,
                identifier_kind='alias',
                canonical_name=canonical_name,
                registry_label=registry_label,
            )
        schema_variant = str(summary.schema_cache_variant or '').strip()
        if not schema_variant:
            continue
        key = schema_variant.casefold()
        existing = schema_variants.get(key)
        if existing is not None:
            raise ValueError(
                f'{registry_label} schema variant collision: '
                f'{schema_variant!r} is claimed by {existing!r} and {canonical_name!r}'
            )
        schema_variants[key] = canonical_name


def _claim_identifier(
    *,
    identifiers: dict[str, tuple[str, str]],
    value: str,
    identifier_kind: str,
    canonical_name: str,
    registry_label: str,
) -> None:
    normalized = str(value or '').strip().casefold()
    if not normalized:
        raise ValueError(
            f'{registry_label} {identifier_kind} is empty for {canonical_name!r}'
        )
    existing = identifiers.get(normalized)
    if existing is not None:
        existing_kind, existing_name = existing
        raise ValueError(
            f'{registry_label} identifier collision: '
            f'{value!r} ({identifier_kind} for {canonical_name!r}) conflicts with '
            f'{existing_kind} for {existing_name!r}'
        )
    identifiers[normalized] = (identifier_kind, canonical_name)
