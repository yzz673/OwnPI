"""Model-facing subagent tool definitions with actor-scoped handlers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ownpi.runtime.contracts import ToolResult
from ownpi.runtime.permissions.policy import FileSystemPolicyMatcher
from ownpi.runtime.tools.definitions import ToolDefinition, ToolExecutionContext, ToolExposure
from ownpi.schemas.runtime_tooling import ToolCallBatchPolicy
from ownpi.runtime.tools.role_policy import tool_role_allowed

from .contracts import SubagentTask


def build_subagent_tool_definitions() -> tuple[ToolDefinition, ToolDefinition]:
    return (_run_definition(), _complete_definition())


def tool_definitions_for_actor(actor_kind: str) -> tuple[ToolDefinition, ...]:
    """Return only the lifecycle tool valid for the requested execution scope."""
    return tuple(
        definition for definition in build_subagent_tool_definitions()
        if tool_role_allowed(definition.name, actor_kind)
    )


def _run_definition() -> ToolDefinition:
    return ToolDefinition(
        name="run_subagents",
        exposure=ToolExposure.DIRECT_MODEL_ONLY,
        kind="subagent_lifecycle",
        description=(
            "Run one or more independent subagent tasks and wait for their results. "
            "Batches exceeding runtime.subagent_max_count are rejected before any child starts. "
            "Results include a complete feedback index and previews. When feedback_truncated "
            "is true, use file_reader on feedback_path and follow pagination to read the full "
            "feedback. feedback_source identifies the committed original call; "
            "unavailable readback does not revoke a submitted completion."
        ),
        detail="Blocking batch boundary for Core and Reviewer.",
        concurrency_safe=False,
        invocation_mode="agent_requested",
        observation_family="subagent.batch",
        result_family="subagent.batch",
        toolset_family="subagent",
        batch_policy=ToolCallBatchPolicy.TURN_EXCLUSIVE,
        backend_kind="synthetic",
        execution_class="local_execution",
        side_effect_level="process_spawn",
        isolation_requirement="process",
        resume_strategy="stateful",
        input_schema_builder=lambda: {
            "type": "object",
            "properties": {
                "tasks": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "task_name": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["task_name", "message"],
                    "additionalProperties": False,
                }},
            },
            "required": ["tasks"],
            "additionalProperties": False,
        },
        input_validator=_validate_run,
        handler=_run_handler,
        strict_schema=True,
    )


def _complete_definition() -> ToolDefinition:
    return ToolDefinition(
        name="complete_subagent",
        exposure=ToolExposure.DIRECT_MODEL_ONLY,
        kind="subagent_lifecycle",
        description="Submit the current subagent result and complete this subagent.",
        detail="Subagent-only completion boundary.",
        batch_policy=ToolCallBatchPolicy.TURN_EXCLUSIVE,
        observation_family="subagent.completion",
        result_family="subagent.completion",
        toolset_family="subagent",
        backend_kind="synthetic",
        execution_class="local_execution",
        side_effect_level="workspace_write",
        isolation_requirement="process",
        resume_strategy="stateful",
        input_schema_builder=lambda: {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        },
        input_validator=_validate_complete,
        handler=_complete_handler,
        strict_schema=True,
    )


def _validate_run(_context: ToolExecutionContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")
    owner = getattr(_context, "subagent_owner", None)
    if owner is not None and owner.enabled:
        owner.validate_batch_size(len(tasks))
    names = []
    for item in tasks:
        if not isinstance(item, dict) or set(item) != {"task_name", "message"}:
            raise ValueError("each task must contain only task_name and message")
        if not isinstance(item["task_name"], str) or not item["task_name"].strip():
            raise ValueError("task_name must be a non-empty string")
        if not isinstance(item["message"], str) or not item["message"].strip():
            raise ValueError("message must be a non-empty string")
        names.append(item["task_name"].strip())
    if len(names) != len(set(names)):
        raise ValueError("task_name values must be unique within a batch")
    return {"tasks": [{"task_name": n, "message": i["message"]} for n, i in zip(names, tasks)]}


def _validate_complete(_context: ToolExecutionContext, payload: dict[str, Any]) -> dict[str, Any] | None:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    return {"content": content.strip()}


async def _run_handler(context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
    if not tool_role_allowed('run_subagents', context.actor_kind):
        raise PermissionError("run_subagents is available only to Core and Reviewer")
    owner = context.subagent_owner
    if owner is None or not owner.enabled:
        raise RuntimeError("subagent mode is disabled")
    tasks = tuple(SubagentTask(**item) for item in payload["tasks"])
    result = await owner.run_batch_async(
        tasks,
        cancel_checker=context.cancel_checker,
        invocation_id=context.tool_call_id,
    )
    from ownpi.runtime.interactions.pause import raise_child_interactions

    raise_child_interactions(result.pending_interactions)
    children = []
    for child in result.children:
        if child.feedback_path is not None:
            permissions = context.effective_permissions
            if permissions is None or not FileSystemPolicyMatcher(permissions.profile.file_system).can_read(
                child.feedback_path, cwd=context.project_root,
            ):
                child = replace(child, feedback_path=None, feedback_error="Caller filesystem policy denied reading feedback")
        children.append(child)
    result = replace(result, children=tuple(children))
    return ToolResult(
        ok=result.status == "completed" and not any(child.feedback_error for child in result.children),
        tool_name="run_subagents",
        data=result.to_dict(),
    )


async def _complete_handler(context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
    if not tool_role_allowed('complete_subagent', context.actor_kind) or not context.actor_id:
        raise PermissionError("complete_subagent is available only to a subagent")
    owner = context.subagent_owner
    if owner is None or not owner.enabled:
        raise RuntimeError("subagent mode is disabled")
    from ownpi.runtime.rollout.call_source import confirm_call_source

    source = confirm_call_source(context, 'complete_subagent')
    result = await owner.complete_async(context.actor_id, payload["content"], source=source)
    return ToolResult(
        ok=True,
        tool_name="complete_subagent",
        data={"task_name": result.task_name, "content": result.content},
    )
