from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from ownpi.runtime.native_tools.hosted_web_search import WebSearchExecutionMode
from ownpi.runtime.native_tools.spec import NativeToolSpec
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExposure
from ownpi.runtime.tools.defaults import build_default_tool_definitions
from ownpi.runtime.tools.defaults_remote import bind_remote_tool_environment
from ownpi.runtime.tools.role_policy import tool_role_allowed


def allow_human_question_from_runtime_state(runtime_state: Any | None) -> bool:
    """Return the persisted policy for starting a new human question."""
    preferences = getattr(runtime_state, 'runtime_preferences', None)
    interaction = getattr(preferences, 'interaction', {})
    if not isinstance(interaction, dict):
        return False
    return bool(interaction.get('allow_human_question', False))


@dataclass(frozen=True)
class ToolEnvironmentSnapshot:
    active_remote_profile: str = ''

    @classmethod
    def from_runtime_state(cls, state: Any | None) -> 'ToolEnvironmentSnapshot':
        if state is None:
            return cls()
        remote_state = getattr(state, 'remote_state', None)
        return cls(
            active_remote_profile=str(
                getattr(remote_state, 'active_remote_profile', '') or ''
            ).strip(),
        )


@dataclass(frozen=True)
class ToolPlanContext:
    tool_search_enabled: bool = True
    supports_image_input: bool = True
    allow_human_question: bool = False
    web_search_execution_mode: WebSearchExecutionMode = WebSearchExecutionMode.LOCAL_FALLBACK
    actor_kind: str = 'core'
    state_scoped_definitions: tuple[ToolDefinition, ...] = ()
    scoped_definitions: tuple[ToolDefinition, ...] = ()
    environment: ToolEnvironmentSnapshot = ToolEnvironmentSnapshot()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'state_scoped_definitions',
            tuple(self.state_scoped_definitions),
        )
        object.__setattr__(self, 'scoped_definitions', tuple(self.scoped_definitions))

    @classmethod
    def from_runtime_state(
        cls,
        runtime_state: Any,
        *,
        tool_search_enabled: bool = True,
        supports_image_input: bool = True,
        allow_human_question: bool = False,
        web_search_execution_mode: WebSearchExecutionMode = (
            WebSearchExecutionMode.LOCAL_FALLBACK
        ),
        actor_kind: str = 'core',
        scoped_definitions: tuple[ToolDefinition, ...] = (),
        environment: ToolEnvironmentSnapshot | None = None,
        internal_memory_snapshot: Any | None = None,
    ) -> 'ToolPlanContext':
        return cls(
            tool_search_enabled=tool_search_enabled,
            supports_image_input=supports_image_input,
            allow_human_question=allow_human_question,
            web_search_execution_mode=web_search_execution_mode,
            actor_kind=actor_kind,
            state_scoped_definitions=_runtime_state_scoped_definitions(
                runtime_state,
                internal_memory_snapshot=internal_memory_snapshot,
            ),
            scoped_definitions=scoped_definitions,
            environment=(
                environment
                if environment is not None
                else ToolEnvironmentSnapshot.from_runtime_state(runtime_state)
            ),
        )


@dataclass(frozen=True)
class PlannedTools:
    definitions: tuple[ToolDefinition, ...] = ()
    hosted_specs: tuple[NativeToolSpec, ...] = ()
    disabled_reasons: tuple[tuple[str, str], ...] = ()

    def add(self, definition: ToolDefinition) -> 'PlannedTools':
        return replace(self, definitions=(*self.definitions, definition))

    def add_with_exposure(
        self,
        definition: ToolDefinition,
        exposure: ToolExposure,
    ) -> 'PlannedTools':
        return self.add(replace(definition, exposure=exposure))

    def add_dispatch_only(self, definition: ToolDefinition) -> 'PlannedTools':
        return self.add_with_exposure(definition, ToolExposure.HIDDEN)

    def add_hosted_spec(self, spec: NativeToolSpec) -> 'PlannedTools':
        return replace(self, hosted_specs=(*self.hosted_specs, spec))

    def disable(self, tool_name: str, reason: str) -> 'PlannedTools':
        return replace(
            self,
            disabled_reasons=(*self.disabled_reasons, (tool_name, reason)),
        )


def plan_tools(context: ToolPlanContext) -> PlannedTools:
    planned = _add_builtin_tool_sources(PlannedTools(), context=context)
    planned = _add_state_scoped_sources(planned, context=context)
    planned = _add_actor_control_sources(planned, context=context)
    allowed = []
    for definition in planned.definitions:
        if tool_role_allowed(definition.name, context.actor_kind):
            allowed.append(definition)
        else:
            planned = planned.disable(definition.name, 'special_tool_role_not_allowed')
    return replace(planned, definitions=tuple(allowed))


def _add_builtin_tool_sources(
    planned: PlannedTools,
    *,
    context: ToolPlanContext,
) -> PlannedTools:
    for definition in build_default_tool_definitions():
        if definition.name == 'human_question' and not context.allow_human_question:
            planned = planned.disable(definition.name, 'human_question_disabled')
            continue
        if definition.name == 'view_image' and not context.supports_image_input:
            planned = planned.disable(
                definition.name,
                'model_input_modality_image_unavailable',
            )
            continue
        if definition.name == 'web_search':
            planned = _add_web_search_source(planned, definition=definition, context=context)
            continue
        planned = planned.add(_project_environment_state(definition, context.environment))
    return planned


def _add_web_search_source(
    planned: PlannedTools,
    *,
    definition: ToolDefinition,
    context: ToolPlanContext,
) -> PlannedTools:
    mode = context.web_search_execution_mode
    if mode is WebSearchExecutionMode.DISABLED:
        return planned.disable('web_search', 'web_search_execution_mode_disabled')
    if mode is WebSearchExecutionMode.HOSTED:
        hosted = replace(
            NativeToolSpec.from_tool_definition(definition),
            hosted_execution_allowed=True,
        )
        return planned.add_hosted_spec(hosted)
    return planned.add(definition)


def _add_state_scoped_sources(
    planned: PlannedTools,
    *,
    context: ToolPlanContext,
) -> PlannedTools:
    for definition in context.state_scoped_definitions:
        planned = planned.add(_project_environment_state(definition, context.environment))
    return planned


def _runtime_state_scoped_definitions(
    runtime_state: Any,
    *,
    internal_memory_snapshot: Any | None = None,
) -> tuple[ToolDefinition, ...]:
    from ownpi.runtime.mcp.dynamic_tools import (
        build_mcp_dynamic_tool_definitions_from_state,
        refresh_mcp_tool_cache_from_configured_servers,
    )
    from ownpi.runtime.tools.defaults_external_knowledge import (
        build_external_knowledge_tool_definitions,
    )
    from ownpi.runtime.tools.defaults_internal_memory import (
        build_internal_memory_tool_definitions,
    )

    refresh_mcp_tool_cache_from_configured_servers(
        runtime_state,
        run_id=str(
            getattr(getattr(runtime_state, 'run_metadata', None), 'run_id', '') or ''
        ),
    )
    return (
        *build_internal_memory_tool_definitions(
            runtime_state,
            repository_snapshot=internal_memory_snapshot,
        ),
        *build_external_knowledge_tool_definitions(runtime_state),
        *build_mcp_dynamic_tool_definitions_from_state(runtime_state),
    )


def _add_actor_control_sources(
    planned: PlannedTools,
    *,
    context: ToolPlanContext,
) -> PlannedTools:
    for definition in context.scoped_definitions:
        planned = planned.add(_project_environment_state(definition, context.environment))
    return planned


def _project_environment_state(
    definition: ToolDefinition,
    environment: ToolEnvironmentSnapshot,
) -> ToolDefinition:
    return bind_remote_tool_environment(
        definition, active_profile=environment.active_remote_profile,
    )


def definitions_by_name(
    definitions: Iterable[ToolDefinition],
) -> dict[str, ToolDefinition]:
    return {definition.name: definition for definition in definitions}


__all__ = [
    'PlannedTools',
    'ToolEnvironmentSnapshot',
    'ToolPlanContext',
    'definitions_by_name',
    'plan_tools',
]
