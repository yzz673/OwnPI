"""Shared production entry points for agents calling blocking subagent batches."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from ownpi.runtime.tools.runtime import ToolRuntimeContext

from .factory import SubagentExecutionFactory
from .owner import SubagentOwner


def owner_for_invocation(owner: Any, *, runtime_config: Any, run_dir: Path) -> Any:
    """Keep one owner per active run session; reconstruct it after a drained shutdown."""
    if not bool(getattr(runtime_config, "subagent_enabled", False)):
        return owner
    if owner is None:
        return SubagentOwner.from_runtime_config(runtime_config)
    store = owner.coordinator.state_store
    expected_scope = run_dir / 'subagents'
    changed_run = store is not None and store.path.resolve() != expected_scope.resolve()
    if owner.is_shutdown or changed_run:
        report = owner.shutdown_report()
        if not report.batch_drained or report.timed_out or report.submit_failed:
            raise RuntimeError("previous subagent owner has not drained")
        # With no live worker, a fresh factory can recover the original run's
        # durable resource checkpoints even if the old process manager was closed.
        return SubagentOwner.from_runtime_config(runtime_config)
    return owner


def prepare_caller(
    context: ToolRuntimeContext, *, level: Any, memory_map: Any,
    llm_client: Any, tool_runtime: Any, canonical_ledger_messages: list[dict[str, Any]],
) -> ToolRuntimeContext:
    config = getattr(context.user_config, "runtime", context.user_config)
    if not bool(getattr(config, "subagent_enabled", False)):
        return replace(context, subagent_owner=None)
    if context.actor_kind not in {"core", "reviewer"}:
        raise ValueError("subagent caller must be Core or Reviewer")
    owner = context.subagent_owner
    # Preserve explicitly injected adapters used by runtime integrations.
    if owner is None or owner.adapter is None or owner.execution_factory is not None:
        factory = SubagentExecutionFactory(
            parent_context=context, child_root=context.run_dir / "subagents",
            level=level, stage=context.stage, memory_map=memory_map,
        )
        owner = factory.build_owner(
            llm_client=llm_client, tool_runtime=tool_runtime,
            canonical_ledger_messages=canonical_ledger_messages,
            tool_specs=context.tool_router.initial_provider_specs,
            existing_owner=owner,
        )
    return replace(context, subagent_owner=owner)
