from __future__ import annotations

from ownpi.skills.bundled.registry import BundledSkillDefinition, register_bundled_skill


def register_mcp_integration_skill() -> None:
    register_bundled_skill(
        BundledSkillDefinition(
            slug='mcp-integration',
            name='mcp-integration',
            description=(
                'This skill should be used when the user asks to "add MCP server", '
                '"integrate MCP", "configure MCP", "set up Model Context Protocol", '
                '"connect an external service", or discusses MCP server types (SSE, stdio, '
                'HTTP, WebSocket). Provides guidance for integrating MCP servers into '
                'OwnPI projects through the canonical runtime.'
            ),
            tags=(),
        )
    )
