from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ownpi.runtime.tools.definition_registry import validate_tool_definition_snapshot
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure


@dataclass(frozen=True)
class ToolRegistry:
    definitions: tuple[ToolDefinition, ...]
    _by_identifier: dict[str, ToolDefinition] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        definitions = tuple(self.definitions)
        validate_tool_definition_snapshot(definitions, registry_label='tool registry')
        identifiers: dict[str, ToolDefinition] = {}
        for definition in definitions:
            for identifier in (definition.name, *definition.aliases):
                identifiers[identifier] = definition
        object.__setattr__(self, 'definitions', definitions)
        object.__setattr__(self, '_by_identifier', identifiers)

    @classmethod
    def from_definitions(
        cls,
        definitions: Iterable[ToolDefinition],
    ) -> 'ToolRegistry':
        return cls(tuple(definitions))

    def resolve(self, tool_name: str) -> ToolDefinition | None:
        return self._by_identifier.get(tool_name)

    def require(self, tool_name: str) -> ToolDefinition:
        definition = self.resolve(tool_name)
        if definition is None:
            raise KeyError(f'unknown tool: {tool_name}')
        return definition

    @property
    def registered_names(self) -> tuple[str, ...]:
        return tuple(definition.name for definition in self.definitions)

    def definitions_with_exposure(
        self,
        exposure: ToolExposure,
    ) -> tuple[ToolDefinition, ...]:
        return tuple(
            definition
            for definition in self.definitions
            if definition.exposure is exposure
        )


__all__ = ['ToolRegistry']
