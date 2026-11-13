# Upstream Provenance

- Source: `~/.codex/skills/mcp-integration/`
- Migrated: 2026-08-03
- License: upstream package does not contain a separate license file
- Exclusions: none

## SHA-256 Manifest

- `SKILL.md`: `1cbfb6b1cf98f48a7c6938e113869a229cd5a59037712072fe11f21a47c6df0e`
- `references/authentication.md`: `9f3ca61a857a15cc4c0dea9143365b0c49cae558690089dd61f6434186c702ce`
- `references/server-types.md`: `7b06c7ea92e255218897189c550a32cbad1daa173e78e40ec94524fc9a1fb36b`
- `references/tool-usage.md`: `8dd0005d7ba6cd2d24e72f9e6102c129b7445fc818e14656ad9ebaef6621a7a6`
- `examples/stdio-server.json`: `46148a5dc7fc5e63c681c5fb975d3908d3f3052f51de7236725cdadda41e2b5d`
- `examples/http-server.json`: `7a11adaea5ac1bb76269bf473f5cf5cbd636a83c2ec0b7007f2b1cbf4a8a725f`
- `examples/sse-server.json`: `9b0be023f74e12c804757e56dc86349134fd7497ef17f34c7c2ff25039cd1efc`

## Adapter Patch

- `SKILL.md`: mapped Claude plugin ownership, `.mcp.json`/`plugin.json` schema,
  `${CLAUDE_PLUGIN_ROOT}`, `/mcp`, debug command, dynamic Tool identity, permission
  wording, and lifecycle wording to the canonical OwnPI MCP Runtime.
- `examples/*.json`: mapped each upstream transport example to the
  `runtime.mcp_servers` list schema accepted by `UserConfig`.
- `references/server-types.md`: mapped transport fields, stdio command tokenization,
  environment/path handling, and Runtime lifecycle names.
- `references/authentication.md`: mapped secret injection and OAuth lifecycle to
  `mcp_auth`, environment/private config, and credential helpers.
- `references/tool-usage.md`: mapped discovery/invocation/resource operations to
  `mcp_tool_list`, `mcp_tool_invoke`, `mcp_resource_list`, and `mcp_resource_read`.
- These are host interface substitutions only; no MCP client, transport, auth backend,
  connector-specific workflow, or implicit Tool grant is introduced.
