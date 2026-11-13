from __future__ import annotations

from pathlib import Path

from ownpi.agents.base import RegisteredAgent
from ownpi.agents.registry import register_agent_role
from ownpi.app.paths import AppPaths
from ownpi.llm.base import LLMClient
from ownpi.runtime.builders import build_agent_input
from ownpi.runtime.contracts import AgentInput, AgentOutput
from ownpi.runtime.state_models import RuntimeState
from ownpi.runtime.stage_execution import run_stage_critic_native_loop
from ownpi.runtime.stage_execution.critic_profile import reviewer_role_profile
from ownpi.schemas.runtime import AgentInstanceConfig


class OwnPIReviewerAgent(RegisteredAgent):
    role_name = "reviewer"
    artifact_type = "stage_review"

    def run(self, agent_input: AgentInput | RuntimeState) -> AgentOutput:
        if isinstance(agent_input, RuntimeState):
            agent_input = build_agent_input(agent_input, self._agent_config)
        if agent_input.operation not in {"reviewer_review", "direct_critic_reviewer"}:
            raise ValueError(
                f"Unsupported reviewer operation: {agent_input.operation!r}"
            )
        return self._run_stage_review(agent_input)

    def _run_stage_review(self, agent_input: AgentInput) -> AgentOutput:
        runtime_context = self.runtime_invocation_context
        if runtime_context is None:
            raise RuntimeError("Reviewer review requires a Runtime invocation context.")
        runner = run_stage_critic_native_loop
        if agent_input.operation == 'direct_critic_reviewer':
            from ownpi.runtime.stage_execution.critic_loop_runner import run_direct_critic_native_loop

            runner = run_direct_critic_native_loop
        output, record = runner(
            invocation_context=runtime_context,
            agent_input=agent_input,
            agent_id=self.agent_id,
            llm_client=self.llm_client,
            app_paths=self.app_paths,
            project_root=self.project_root,
            profile=reviewer_role_profile(),
        )
        return AgentOutput(
            artifact_type="stage_review",
            payload=record,
            summary=output.summary,
            status=output.status,
            needs_review=False,
            next_action=output.next_action,
            metadata=output.metadata,
        )


def _build_ownpi_reviewer_agent(
    agent_config: AgentInstanceConfig,
    llm_client: LLMClient,
    project_root: Path,
    app_paths: AppPaths,
) -> OwnPIReviewerAgent:
    return OwnPIReviewerAgent(
        agent_config=agent_config,
        llm_client=llm_client,
        project_root=project_root,
        app_paths=app_paths,
    )


register_agent_role("reviewer", _build_ownpi_reviewer_agent)
