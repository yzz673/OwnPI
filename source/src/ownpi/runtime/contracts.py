from __future__ import annotations

# ruff: noqa: E402

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ownpi.runtime.model_content import ModelContentItem, content_items_from_dicts, content_items_to_dicts
from ownpi.controller.stages import Stage


from ownpi.runtime.events import HistoryEvent
from ownpi.schemas.runtime import AgentInstanceConfig
from ownpi.schemas.runtime_tooling import ToolABI, ToolDescriptorSummary
from ownpi.schemas.workflow_plan import (
    ExecutionTier,
    RunWorkflowPlan,
    StageRuntimeProfile,
)

ToolDescriptor = ToolDescriptorSummary

if TYPE_CHECKING:
    from ownpi.runtime.memory.map_contracts import MemoryMap
    from ownpi.runtime.native_tools.loaded_tools import NativeLoadedToolRefV1
    from ownpi.runtime.permissions.models import EffectivePermissions
    from ownpi.runtime.tools.router import ToolRouter



@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    artifact_type: str
    file_path: str | None = None
    summary: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ArtifactRef':
        return cls(
            artifact_id=str(data.get('artifact_id', '')),
            artifact_type=str(data.get('artifact_type', '')).strip(),
            file_path=(str(data['file_path']) if data.get('file_path') is not None else None),
            summary=(str(data['summary']) if data.get('summary') is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    producer_agent: str
    created_at: str
    source_paths: list[str] = field(default_factory=list)
    summary: str = ''
    payload: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    stage_context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ArtifactRecord':
        stage_context = (
            dict(data.get('stage_context', {}))
            if isinstance(data.get('stage_context'), dict)
            else {}
        )
        return cls(
            artifact_id=str(data.get('artifact_id', '')),
            artifact_type=str(data.get('artifact_type', '')).strip(),
            producer_agent=str(data.get('producer_agent', '')),
            created_at=str(data.get('created_at', '')),
            source_paths=[str(item) for item in data.get('source_paths', []) if str(item).strip()],
            summary=str(data.get('summary', '')),
            payload=dict(data.get('payload', {})) if isinstance(data.get('payload'), dict) else {},
            tags=[str(item) for item in data.get('tags', []) if str(item).strip()],
            pinned=bool(data.get('pinned', False)),
            stage_context=stage_context,
        )

    def to_ref(self) -> ArtifactRef:
        file_path = self.source_paths[0] if self.source_paths else None
        return ArtifactRef(
            artifact_id=self.artifact_id,
            artifact_type=self.artifact_type,
            file_path=file_path,
            summary=self.summary or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowView:
    execution_tier: ExecutionTier
    current_stage: Stage
    current_stage_attempt: int
    run_workflow_plan: RunWorkflowPlan | None
    pending_human_clarification: dict[str, Any] | None = None
    pending_tool_permission: dict[str, Any] | None = None
    revisit_context: dict[str, Any] | None = None
    stage_runtime_profile: StageRuntimeProfile = StageRuntimeProfile.TASK_LEVEL
    relevant_history: list[HistoryEvent] = field(default_factory=list)


@dataclass(frozen=True)
class CriticView:
    latest_feedback_record: dict[str, Any] | None = None
    critic_loop: dict[str, Any] = field(default_factory=dict)
    total_stage_attempts: int = 0


@dataclass(frozen=True)
class ToolContext:
    available_tools: list[ToolDescriptor] = field(default_factory=list)
    active_tool_abis: tuple[ToolABI, ...] = ()
    native_loaded_tool_refs: tuple[NativeLoadedToolRefV1, ...] = ()
    selection_summary: str | None = None
    effective_permissions: 'EffectivePermissions | None' = None
    active_remote_host: str | None = None
    router: 'ToolRouter | None' = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class AgentSemanticContext:
    raw_user_message: str
    task_spec: str
    stage_contract: str = ''
    source_roles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentInput:
    role: str
    agent_id: str
    project_workspace_root: str
    managed_venv_path: str
    managed_non_python_dependency_root: str
    semantic_context: AgentSemanticContext
    workflow_view: WorkflowView
    critic_view: CriticView
    tool_context: ToolContext
    operation: str = 'core_stage_round'
    memory_map: MemoryMap | None = None
    runtime_preferences: Any = None
    run_metadata: Any = None
    environment_state: Any = None
    remote_state: Any = None
    artifact_state: Any = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOutput:
    artifact_type: str
    payload: Any
    summary: str
    status: str = 'success'
    needs_review: bool = False
    next_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload.to_dict() if hasattr(self.payload, 'to_dict') else self.payload
        return {
            'artifact_type': self.artifact_type,
            'payload': payload,
            'summary': self.summary,
            'status': self.status,
            'needs_review': self.needs_review,
            'next_action': self.next_action,
            'metadata': dict(self.metadata),
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    tool_name: str
    data: dict[str, Any] | None = None
    content_items: tuple[ModelContentItem, ...] = ()
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    duration: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ToolResult':
        artifacts = data.get('artifacts', [])
        duration = data.get('duration')
        return cls(
            ok=bool(data.get('ok', False)),
            tool_name=str(data.get('tool_name', '')),
            data=dict(data.get('data', {})) if isinstance(data.get('data'), dict) else None,
            content_items=content_items_from_dicts(data.get('content_items')),
            stdout=(str(data['stdout']) if data.get('stdout') is not None else None),
            stderr=(str(data['stderr']) if data.get('stderr') is not None else None),
            exit_code=(int(data['exit_code']) if data.get('exit_code') is not None else None),
            artifacts=[ArtifactRef.from_dict(item) for item in artifacts if isinstance(item, dict)],
            metadata=dict(data.get('metadata', {})) if isinstance(data.get('metadata'), dict) else {},
            warnings=[str(item) for item in data.get('warnings', []) if str(item).strip()],
            error=(str(data['error']) if data.get('error') is not None else None),
            duration=(float(duration) if duration is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'ok': self.ok,
            'tool_name': self.tool_name,
            'data': self.data,
            'content_items': content_items_to_dicts(self.content_items, redact_image_url=True),
            'stdout': self.stdout,
            'stderr': self.stderr,
            'exit_code': self.exit_code,
            'artifacts': [item.to_dict() for item in self.artifacts],
            'metadata': dict(self.metadata),
            'warnings': list(self.warnings),
            'error': self.error,
            'duration': self.duration,
        }


RuntimeAgent = tuple[AgentInstanceConfig, Any]
