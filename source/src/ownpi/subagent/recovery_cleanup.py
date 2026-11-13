"""Checkpoint child resource facts without managing process lifetimes."""

from __future__ import annotations

import asyncio

from ownpi.runtime.tools.runtime import ToolRuntimeContext


def checkpoint_child_resources(context: ToolRuntimeContext) -> None:
    asyncio.run(checkpoint_child_resources_async(context))


async def checkpoint_child_resources_async(context: ToolRuntimeContext) -> None:
    from ownpi.runtime.rollout.child_resource_events import record_child_resources

    await record_child_resources(context)
