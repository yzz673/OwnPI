from __future__ import annotations

from ownpi.runtime.limits.projection import (
    MAX_NAMESPACE_DESCRIPTION_CHARS,
    MAX_TOOL_NAVIGATION_BYTES,
)
from ownpi.runtime.tools.router import ToolRouter

_NAMESPACE_DESCRIPTIONS = {
    'generic': 'Code intelligence, runtime task management, and MCP resource templates.',
    'external': 'MCP resources, tool discovery and invocation, and authentication.',
    'literature': 'Retrieve research papers.',
    'artifact': 'Resolve citations and validate citation packages.',
    'coding': 'Build and inspect documents, and obtain model and repository resources.',
    'remote_workflow': 'Inspect remote environments, deploy and run work, and retrieve results.',
    'local_process': 'Monitor local executions, read their logs, and stop them.',
}
_INTRO = (
    'Deferred tool namespaces:\n'
    'These capabilities have tools whose definitions are not preloaded. '
    'Use tool_search to find tools, then call them using their loaded definitions.\n'
)


def render_tool_navigation(router: ToolRouter) -> str:
    """Render bounded discovery guidance from the effective deferred index."""
    if not router.deferred_search_entries:
        return ''
    namespaces: dict[str, str] = {}
    for entry in router.deferred_search_entries:
        description = (
            entry.namespace_description
            if entry.namespace.startswith('mcp:')
            else _NAMESPACE_DESCRIPTIONS.get(entry.namespace, '')
        )
        description = ' '.join(description.split())[:MAX_NAMESPACE_DESCRIPTION_CHARS]
        # Resolve conflicting source descriptions independently of tool registration order.
        previous = namespaces.get(entry.namespace, '')
        namespaces[entry.namespace] = min(previous, description) if previous and description else (
            previous or description
        )
    lines = [_INTRO]
    used_bytes = len(_INTRO.encode('utf-8'))
    omitted = 0
    reserve = len(_omission_notice(len(namespaces)).encode('utf-8'))
    for namespace, description in sorted(namespaces.items()):
        line = f'- {namespace}' + (f': {description}' if description else '') + '\n'
        size = len(line.encode('utf-8'))
        if used_bytes + size + reserve > MAX_TOOL_NAVIGATION_BYTES:
            omitted += 1
            continue
        lines.append(line)
        used_bytes += size
    if omitted:
        lines.append(_omission_notice(omitted))
    return ''.join(lines).rstrip()


def _omission_notice(count: int) -> str:
    return f'... {count} additional namespaces omitted; their tools remain searchable.\n'


__all__ = ["render_tool_navigation"]
