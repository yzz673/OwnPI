"""Actor restrictions belong only to workflow and lifecycle control tools."""

from types import MappingProxyType


SPECIAL_TOOL_ROLES = MappingProxyType({
    'request_review': frozenset({'core'}),
    'complete_feedback': frozenset({'core'}),
    'next_stage': frozenset({'core'}),
    # The feedback handler additionally checks the bound author and stage.
    'submit_feedback': frozenset({'core', 'reviewer'}),
    'wait_for_execution_update': frozenset({'core'}),
    'run_subagents': frozenset({'core', 'reviewer'}),
    'complete_subagent': frozenset({'subagent'}),
})


def tool_role_allowed(tool_name: str, actor_kind: str) -> bool:
    """Allow ordinary tools for every actor, including newly introduced roles."""
    roles = SPECIAL_TOOL_ROLES.get(tool_name.strip().casefold())
    return roles is None or actor_kind in roles
