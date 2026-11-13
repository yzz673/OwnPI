from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.limits.projection import MAX_FILE_READER_MODEL_VISIBLE_BYTES
from ownpi.schemas.runtime_tooling import (
    ToolABI,
    ToolCallBatchPolicy,
    ToolDescriptorSummary,
)

if TYPE_CHECKING:
    from ownpi.runtime.native_tools.search import NativeToolSearchEntry
    from ownpi.runtime.permissions.action_compiler import OperationEffects
    from ownpi.skills.snapshot import SkillCatalogSnapshot


ToolHandler = Callable[['ToolExecutionContext', dict[str, Any]], Awaitable[ToolResult]]
ToolInputValidator = Callable[['ToolExecutionContext', dict[str, Any]], dict[str, Any] | None]
ToolResultSummaryBuilder = Callable[[ToolResult], str | None]
ToolActivityBuilder = Callable[[dict[str, Any]], str | None]
ToolClassifierInputBuilder = Callable[[dict[str, Any]], str | dict[str, Any] | None]
ToolEffectBuilder = Callable[
    ['ToolExecutionContext', dict[str, Any]],
    'OperationEffects',
]


class ToolExposure(str, Enum):
    DIRECT = 'direct'
    DEFERRED = 'deferred'
    DIRECT_MODEL_ONLY = 'direct_model_only'
    HIDDEN = 'hidden'

    @property
    def is_direct(self) -> bool:
        return self in {ToolExposure.DIRECT, ToolExposure.DIRECT_MODEL_ONLY}

    @property
    def is_searchable(self) -> bool:
        return self is ToolExposure.DEFERRED


@dataclass(frozen=True)
class ToolExecutionContext:
    raw_user_message: str
    task_spec_text: str
    project_root: Path
    stage: Any
    attempt: int
    continuation_index: int = 0
    run_id: str = ''
    tool_call_id: str = ''
    rollout_history: Any | None = None
    dataset_path_override: str | None = None
    run_dir: Path | None = None
    state_store: Any | None = None
    execution_runtime_service: Any | None = None
    environment_config_snapshot: dict[str, Any] | None = None
    environment_descriptor: dict[str, Any] | None = None
    sandbox_policy: dict[str, Any] | None = None
    environment_provider_kind: str = 'host_local'
    environment_selection_reason: str = ''
    environment_affinity: str = 'host_preferred'
    execution_class: str = 'read_only'
    user_config: Any | None = None
    actor_kind: str = 'core'
    actor_id: str | None = None
    process_owner_actor_id: str | None = None
    execution_handle: Any | None = None
    active_remote_profile: str = ''
    cancel_checker: Callable[[], bool] | None = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None
    mcp_elicitation_handler: Callable[[Any], Any] | None = None
    hook_engine: Any | None = None
    app_paths: Any | None = None
    execution_contract_id: str = ''
    execution_contract_hash: str = ''
    process_owner_manager: Any | None = None
    exec_policy_decision_id: str = ''
    exec_policy_hash: str = ''
    canonical_action_hash: str = ''
    canonical_effect_hash: str = ''
    effective_permissions: Any | None = None
    runtime_text_resource_records: tuple[Any, ...] = ()
    skill_catalog_snapshot: SkillCatalogSnapshot | None = None
    subagent_owner: Any | None = None


@dataclass(frozen=True)
class ToolCapabilityContext:
    stage: str
    execution_tier: str = 'full'
    network_mode: str = 'unknown'
    workspace_policy: str = 'workspace_write'
    active_toolsets: tuple[str, ...] = ()
    available_backends: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    exposure: ToolExposure
    kind: str = 'generic'
    description: str | None = None
    detail: str | None = None
    aliases: tuple[str, ...] = ()
    search_hint: str | None = None
    search_keywords: tuple[str, ...] = ()
    namespace_description: str = ''
    read_only: bool = False
    concurrency_safe: bool = False
    batch_policy: ToolCallBatchPolicy = ToolCallBatchPolicy.COMPATIBLE
    permission_class: str = 'default'
    invocation_mode: str = 'agent_requested'
    handler: ToolHandler | None = None
    detail_builder: Callable[[], dict[str, Any]] | None = None
    tool_abi_builder: Callable[[], ToolABI] | None = None
    input_schema_builder: Callable[[], dict[str, Any]] | None = None
    input_validator: ToolInputValidator | None = None
    summary_builder: ToolResultSummaryBuilder | None = None
    activity_builder: ToolActivityBuilder | None = None
    classifier_input_builder: ToolClassifierInputBuilder | None = None
    effect_builder: ToolEffectBuilder | None = None
    observation_family: str = 'generic'
    result_family: str = 'generic'
    writes_workspace: bool = False
    requires_network: bool = False
    produces_artifact_refs: bool = False
    toolset_family: str = 'generic'
    backend_kind: str = 'generic'
    environment_affinity: str = 'host_preferred'
    execution_class: str = 'read_only'
    side_effect_level: str = 'none'
    isolation_requirement: str = 'in_process'
    credential_mode: str = 'none'
    resume_strategy: str = 'stateless'
    destructive_capable: bool = False
    destructive_kinds: tuple[str, ...] = ()
    max_prompt_result_chars: int = 900
    max_result_size_chars: int = 0
    aggregate_budget_weight: int = 1
    result_identity_fields: tuple[str, ...] = ()
    working_set_priority: str = 'normal'
    permission_matcher_kind: str = 'default'
    observable_input_fields: tuple[str, ...] = ()
    secret_input_fields: tuple[str, ...] = ()
    interrupt_behavior: str = ''
    schema_cache_variant: str = ''
    strict_schema: bool = True
    action_primitives: tuple[str, ...] = ()
    primary_intent: str = ''
    grounding_lane: str = 'open'
    input_contract_family: str = ''
    selection_disambiguators: tuple[str, ...] = ()
    preferred_predecessors: tuple[str, ...] = ()
    preferred_successors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.exposure, ToolExposure):
            raise TypeError('tool exposure must be a ToolExposure value')
        if not self.name.strip():
            raise ValueError('tool name is required')
        if not isinstance(self.batch_policy, ToolCallBatchPolicy):
            object.__setattr__(
                self,
                'batch_policy',
                ToolCallBatchPolicy(str(self.batch_policy or ToolCallBatchPolicy.COMPATIBLE)),
            )
        if self.exposure.is_searchable and not any(
            (
                str(self.description or '').strip(),
                str(self.detail or '').strip(),
                str(self.search_hint or '').strip(),
                self.search_keywords,
            )
        ):
            raise ValueError(f'deferred tool {self.name!r} requires search metadata')
        object.__setattr__(
            self,
            'toolset_family',
            _prefer_contract_value(self.toolset_family, '', _infer_toolset_family(self)),
        )
        object.__setattr__(
            self,
            'backend_kind',
            _prefer_contract_value(self.backend_kind, '', _infer_backend_kind(self)),
        )
        object.__setattr__(
            self,
            'side_effect_level',
            _prefer_contract_value(self.side_effect_level, '', _infer_side_effect_level(self)),
        )
        object.__setattr__(
            self,
            'credential_mode',
            _prefer_contract_value(self.credential_mode, '', _infer_credential_mode(self)),
        )
        object.__setattr__(
            self,
            'max_prompt_result_chars',
            _prefer_budget_int(
                self.max_prompt_result_chars,
                0,
                _infer_max_prompt_result_chars(self),
                default=900,
            ),
        )
        object.__setattr__(
            self,
            'max_result_size_chars',
            _prefer_budget_int(
                self.max_result_size_chars,
                0,
                _infer_max_prompt_result_chars(self),
                default=0,
            ),
        )
        object.__setattr__(
            self,
            'aggregate_budget_weight',
            _prefer_budget_int(
                self.aggregate_budget_weight,
                0,
                _infer_aggregate_budget_weight(self),
                default=1,
            ),
        )
        object.__setattr__(
            self,
            'working_set_priority',
            _prefer_budget_value(
                self.working_set_priority,
                '',
                _infer_working_set_priority(self),
                default='normal',
            ),
        )
        object.__setattr__(self, 'interrupt_behavior', _resolve_interrupt_behavior(self))

    @property
    def summary(self) -> ToolDescriptorSummary:
        return self.to_summary()

    def to_summary(self) -> ToolDescriptorSummary:
        return ToolDescriptorSummary(
            name=self.name,
            kind=self.kind,
            description=self.description,
            detail=self.detail,
            aliases=self.aliases,
            search_hint=self.search_hint,
            search_keywords=self.search_keywords,
            max_prompt_result_chars=self.max_prompt_result_chars,
            max_result_size_chars=self.max_result_size_chars,
            aggregate_budget_weight=self.aggregate_budget_weight,
            result_identity_fields=self.result_identity_fields,
            working_set_priority=self.working_set_priority,
            read_only=self.read_only,
            concurrency_safe=self.concurrency_safe,
            batch_policy=self.batch_policy,
            interrupt_behavior=self.interrupt_behavior,
            permission_class=self.permission_class,
            permission_matcher_kind=self.permission_matcher_kind,
            observable_input_fields=self.observable_input_fields,
            secret_input_fields=self.secret_input_fields,
            invocation_mode=self.invocation_mode,
            result_family=self.result_family,
            writes_workspace=self.writes_workspace,
            requires_network=self.requires_network,
            produces_artifact_refs=self.produces_artifact_refs,
            toolset_family=self.toolset_family,
            backend_kind=self.backend_kind,
            environment_affinity=self.environment_affinity,
            execution_class=self.execution_class,
            side_effect_level=self.side_effect_level,
            isolation_requirement=self.isolation_requirement,
            credential_mode=self.credential_mode,
            resume_strategy=self.resume_strategy,
            destructive_capable=self.destructive_capable,
            destructive_kinds=self.destructive_kinds,
            schema_cache_variant=self.schema_cache_variant,
            strict_schema=self.strict_schema,
            action_primitives=self.action_primitives,
            primary_intent=self.primary_intent,
            grounding_lane=self.grounding_lane,
            input_contract_family=self.input_contract_family,
            selection_disambiguators=self.selection_disambiguators,
            preferred_predecessors=self.preferred_predecessors,
            preferred_successors=self.preferred_successors,
        )

    @property
    def is_concurrency_safe(self) -> bool:
        return self.concurrency_safe

    def tool_abi(self) -> ToolABI:
        cached = getattr(self, '_cached_tool_abi', None)
        if isinstance(cached, ToolABI):
            return cached
        if self.tool_abi_builder is not None:
            abi = self.tool_abi_builder()
            object.__setattr__(self, '_cached_tool_abi', abi)
            return abi
        schema = self.input_schema_builder() if self.input_schema_builder is not None else {}
        if not isinstance(schema, dict):
            schema = {}
        abi = ToolABI(
            tool_name=self.name,
            input_schema=schema,
            schema_name=f'{self.name}_input',
            description=self.description or self.detail,
            strict=True,
        )
        object.__setattr__(self, '_cached_tool_abi', abi)
        return abi

    def parse_arguments(self, raw_arguments: str) -> dict[str, Any]:
        """Decode this tool's function arguments without changing provider facts."""
        from ownpi.runtime.tools.input_validation import parse_function_arguments

        return parse_function_arguments(raw_arguments)

    def validate_input(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        from ownpi.runtime.tools.input_validation import validate_tool_input

        return validate_tool_input(self, arguments)

    def search_info(self) -> 'NativeToolSearchEntry | None':
        if not self.exposure.is_searchable:
            return None
        from ownpi.runtime.native_tools.search import NativeToolSearchEntry

        return NativeToolSearchEntry.from_definition(self)

    def validate_business_input(
        self, context: ToolExecutionContext, arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        from ownpi.runtime.tools.input_validation import validate_business_input

        return validate_business_input(
            definition=self, execution_context=context, input=arguments,
        )

    def resolve_declared_effects(
        self,
        context: ToolExecutionContext,
        input_payload: dict[str, Any],
    ) -> 'OperationEffects | None':
        if self.effect_builder is None:
            return None
        return self.effect_builder(context, dict(input_payload))


def _prefer_contract_value(primary: str, secondary: str, fallback: str) -> str:
    for candidate in (primary, secondary):
        normalized = str(candidate or '').strip()
        if normalized and normalized not in {'generic', 'none'}:
            return normalized
    return fallback


def _prefer_budget_value(primary: str, secondary: str, fallback: str, *, default: str) -> str:
    for candidate in (primary, secondary):
        normalized = str(candidate or '').strip()
        if normalized and normalized != default:
            return normalized
    return fallback


def _prefer_budget_int(primary: int, secondary: int, fallback: int, *, default: int) -> int:
    for candidate in (primary, secondary):
        value = int(candidate or 0)
        if value > 0 and value != default:
            return value
    return fallback


def _infer_max_prompt_result_chars(definition: ToolDefinition) -> int:
    name = definition.name
    if name == 'file_reader':
        return MAX_FILE_READER_MODEL_VISIBLE_BYTES
    if name == 'pdf_reader':
        return 2200
    if name in {'exec_command'}:
        return 900
    return 900


def _infer_aggregate_budget_weight(definition: ToolDefinition) -> int:
    name = definition.name
    if name == 'file_reader':
        return 3
    if name == 'pdf_reader':
        return 4
    if name in {'exec_command'}:
        return 5
    return 1


def _infer_working_set_priority(definition: ToolDefinition) -> str:
    name = definition.name
    if name in {'file_reader', 'pdf_reader'}:
        return 'high'
    return 'low'


def _infer_interrupt_behavior(definition: ToolDefinition) -> str:
    backend_kind = definition.backend_kind or _infer_backend_kind(definition)
    if definition.writes_workspace:
        return 'block'
    if backend_kind == 'local_exec':
        return 'block'
    return 'cancel'


def _resolve_interrupt_behavior(definition: ToolDefinition) -> str:
    explicit = str(getattr(definition, 'interrupt_behavior', '') or '').strip()
    if explicit in {'cancel', 'block'}:
        return explicit
    inferred = _infer_interrupt_behavior(definition)
    if explicit == 'cancel':
        return 'cancel'
    if explicit == 'block' and inferred == 'block':
        return 'block'
    return inferred


def _infer_backend_kind(definition: ToolDefinition) -> str:
    name = definition.name
    if definition.invocation_mode == 'synthetic' or name == 'permission_request':
        return 'synthetic'
    if name.startswith('mcp_'):
        return 'mcp'
    if name.startswith('web_') or name.startswith('zotero_') or name == 'paper_fetch':
        return 'web'
    if name.startswith('artifact_') or name.startswith('memory_') or name.startswith('citation_'):
        return 'artifact'
    if definition.writes_workspace:
        return 'local_write'
    if name.endswith('_runner'):
        return 'local_exec'
    return 'local_read'


def _infer_toolset_family(definition: ToolDefinition) -> str:
    name = definition.name
    if name == 'human_question':
        return 'control'
    if definition.invocation_mode == 'synthetic' or name == 'permission_request':
        return 'control'
    if name.startswith('task_'):
        return 'control'
    if name.startswith('web_') or name.startswith('zotero_') or name in {'paper_fetch', 'pdf_reader'}:
        return 'literature'
    if name.startswith('mcp_'):
        return 'external'
    if name.startswith('artifact_') or name.startswith('memory_') or name.startswith('citation_'):
        return 'artifact'
    if definition.writes_workspace or name.endswith('_runner') or name in {
        'file_reader',
        'notebook_edit',
        'view_image',
    }:
        return 'coding'
    return 'generic'


def _infer_side_effect_level(definition: ToolDefinition) -> str:
    backend_kind = definition.backend_kind or _infer_backend_kind(definition)
    if definition.writes_workspace:
        return 'workspace_write'
    if backend_kind == 'local_exec':
        return 'process_spawn'
    if definition.requires_network or backend_kind in {'web', 'browser', 'mcp'}:
        return 'network_call'
    return 'none'

def _infer_credential_mode(definition: ToolDefinition) -> str:
    backend_kind = definition.backend_kind or _infer_backend_kind(definition)
    if backend_kind == 'mcp' or definition.name.startswith('zotero_'):
        return 'backend_proxy'
    if backend_kind in {'web', 'browser'}:
        return 'host_only'
    return 'none'
