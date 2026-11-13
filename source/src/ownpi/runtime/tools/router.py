from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Iterable

from ownpi.runtime.native_tools.search import (
    NativeToolSearchEntry,
    build_native_tool_search_definition,
)
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.runtime.tools.planning import PlannedTools, ToolPlanContext, plan_tools
from ownpi.runtime.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolRouter:
    _planned: PlannedTools = field(repr=False)
    tool_search_enabled: bool
    registry: ToolRegistry
    model_visible_specs: tuple[NativeToolSpec, ...]
    deferred_search_entries: tuple[NativeToolSearchEntry, ...]
    hosted_specs: tuple[NativeToolSpec, ...]
    disabled_reasons: tuple[tuple[str, str], ...]
    fingerprint: str

    @classmethod
    def build(
        cls,
        planned: PlannedTools,
        *,
        tool_search_enabled: bool,
    ) -> 'ToolRouter':
        definitions = [
            replace(definition, exposure=ToolExposure.DIRECT)
            if not tool_search_enabled and definition.exposure is ToolExposure.DEFERRED
            else definition
            for definition in planned.definitions
        ]
        entries: list[NativeToolSearchEntry] = []
        for definition in definitions:
            if definition.exposure is not ToolExposure.DEFERRED:
                continue
            entry = definition.search_info()
            if entry is None:
                raise ValueError(f'deferred tool {definition.name!r} has no search entry')
            entries.append(entry)
        deferred_entries = tuple(entries)
        if deferred_entries:
            definitions.append(build_native_tool_search_definition(deferred_entries))
        registry = ToolRegistry.from_definitions(definitions)
        model_visible_specs = tuple(
            NativeToolSpec.from_tool_definition(definition)
            for definition in registry.definitions
            if definition.exposure.is_direct
        )
        fingerprint = _router_fingerprint(
            registry=registry,
            model_visible_specs=model_visible_specs,
            deferred_entries=deferred_entries,
            hosted_specs=planned.hosted_specs,
        )
        return cls(
            _planned=planned,
            tool_search_enabled=tool_search_enabled,
            registry=registry,
            model_visible_specs=model_visible_specs,
            deferred_search_entries=deferred_entries,
            hosted_specs=planned.hosted_specs,
            disabled_reasons=planned.disabled_reasons,
            fingerprint=fingerprint,
        )

    @classmethod
    def from_context(cls, context: ToolPlanContext) -> 'ToolRouter':
        return cls.build(
            plan_tools(context),
            tool_search_enabled=context.tool_search_enabled,
        )

    def resolve(self, tool_name: str) -> ToolDefinition | None:
        return self.registry.resolve(tool_name)

    @property
    def planned_definitions(self) -> tuple[ToolDefinition, ...]:
        """Retain declarations when deriving another actor's effective tool surface."""
        return self._planned.definitions

    def with_scoped_definitions(
        self,
        definitions: Iterable[ToolDefinition],
        *,
        tool_search_enabled: bool,
    ) -> 'ToolRouter':
        scoped = tuple(definitions)
        return self.build(
            replace(
                self._planned,
                definitions=(*self._planned.definitions, *scoped),
            ),
            tool_search_enabled=tool_search_enabled,
        )

    @property
    def registered_names(self) -> tuple[str, ...]:
        return self.registry.registered_names

    @property
    def visible_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.model_visible_specs)

    @property
    def deferred_names(self) -> tuple[str, ...]:
        return tuple(entry.canonical_name for entry in self.deferred_search_entries)

    @property
    def hosted_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.hosted_specs)

    @property
    def initial_provider_specs(self) -> tuple[NativeToolSpec, ...]:
        return (*self.model_visible_specs, *self.hosted_specs)

    def trace_metadata(self) -> dict[str, object]:
        return {
            'fingerprint': self.fingerprint,
            'registered_names': list(self.registered_names),
            'visible_names': list(self.visible_names),
            'deferred_names': list(self.deferred_names),
            'hosted_names': list(self.hosted_names),
            'disabled_reasons': dict(self.disabled_reasons),
        }


def _router_fingerprint(
    *,
    registry: ToolRegistry,
    model_visible_specs: tuple[NativeToolSpec, ...],
    deferred_entries: tuple[NativeToolSearchEntry, ...],
    hosted_specs: tuple[NativeToolSpec, ...],
) -> str:
    payload = {
        'registered': [
            {
                'name': definition.name,
                'exposure': definition.exposure.value,
                'schema_variant': definition.schema_cache_variant,
            }
            for definition in registry.definitions
        ],
        'visible': [spec.provider_neutral_schema() for spec in model_visible_specs],
        'deferred': [
            {
                'name': entry.canonical_name,
                'digest': entry.digest,
                'namespace_description': entry.namespace_description,
            }
            for entry in deferred_entries
        ],
        'hosted': [spec.provider_neutral_schema() for spec in hosted_specs],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


__all__ = ['ToolRouter']
