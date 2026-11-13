from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ownpi.app.paths import AppPaths
from ownpi.llm.base import LLMClient
from ownpi.runtime.contracts import AgentInput, AgentOutput
from ownpi.runtime.state_models import RuntimeState
from ownpi.schemas.runtime import AgentInstanceConfig
from ownpi.runtime.rollout.resume_read_session import ResumeReadSession

WorkflowState = RuntimeState
AgentRunResult = AgentOutput


@dataclass(frozen=True)
class AgentRuntimeInvocationContext:
    state_store: Any
    run_dir: Path
    saved_files: list[str]
    stage: Any
    attempt: int
    operation: str
    tool_runtime: Any | None = None
    user_config: Any | None = None
    app_paths: AppPaths | None = None
    hook_engine: Any | None = None
    execution_runtime_service: Any | None = None
    process_owner_manager: Any | None = None
    cancel_checker: Any | None = None
    subagent_owner: Any | None = None
    native_loop_continuation: Any | None = None
    resume_conversation_id: str | None = None
    resume_read_session: ResumeReadSession | None = None


class RegisteredAgent(ABC):
    role_name: str
    artifact_type: str

    def __init__(
        self,
        agent_config: AgentInstanceConfig,
        llm_client: LLMClient,
        project_root: Path,
        app_paths: AppPaths,
    ) -> None:
        self._agent_config = agent_config
        self._llm_client = llm_client
        self._project_root = project_root
        self._app_paths = app_paths
        self._runtime_invocation_context: AgentRuntimeInvocationContext | None = None

    @property
    def agent_id(self) -> str:
        return self._agent_config.id

    @property
    def display_name(self) -> str:
        return self._agent_config.name

    @property
    def project_root(self) -> Path:
        context = self._runtime_invocation_context
        if context is not None:
            state_store = getattr(context, 'state_store', None)
            runtime_root = getattr(state_store, 'project_root', None)
            if isinstance(runtime_root, Path):
                return runtime_root
        return self._project_root

    @property
    def app_paths(self) -> AppPaths:
        return self._app_paths

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @property
    def options(self) -> dict[str, Any]:
        return dict(self._agent_config.options)

    @property
    def runtime_invocation_context(self) -> AgentRuntimeInvocationContext | None:
        return self._runtime_invocation_context

    def set_runtime_invocation_context(
        self,
        context: AgentRuntimeInvocationContext | None,
    ) -> AgentRuntimeInvocationContext | None:
        previous = self._runtime_invocation_context
        self._runtime_invocation_context = context
        return previous

    @abstractmethod
    def run(self, agent_input: AgentInput) -> AgentOutput:
        raise NotImplementedError
