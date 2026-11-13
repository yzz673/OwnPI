---
name: mcp-integration
description: This skill should be used when the user asks to "add MCP server", "integrate MCP", "configure MCP", "set up Model Context Protocol", "connect an external service", or discusses MCP server types (SSE, stdio, HTTP, WebSocket). Provides guidance for integrating MCP servers into OwnPI projects through the canonical runtime.
version: 0.1.0
---

# MCP Integration for OwnPI Projects

## OwnPI Adapter Contract

Use `runtime.mcp_servers` in the user configuration as the only server configuration authority. Each server is an object in a list and must contain a unique `name` and a supported `transport` (`stdio`, `http`, `sse`, or `ws`). Use a list of command tokens for `stdio`, for example `command: [python, -m, my_mcp_server]`; do not split a command into a string plus an `args` field. Set `cwd` explicitly for project-relative executables. Supply credential values through the environment or the configured credential helper; do not put secrets in skill files, traces, or committed configuration.

Use the existing runtime tools for the lifecycle:

- `mcp_tool_list` to discover servers and tools.
- `mcp_tool_invoke` to invoke a discovered MCP tool.
- `mcp_resource_list` and `mcp_resource_read` for resources.
- `mcp_auth` for OAuth or configured authentication.

Dynamic tool names use `mcp__<server_name>__<tool_name>`. Their availability is determined by the runtime MCP permission state and live capability discovery. Configuration changes follow the existing runtime refresh/restart authority.

## Overview

Model Context Protocol (MCP) enables OwnPI projects to integrate with external services and APIs by providing structured tool access. Use this skill to configure and verify the existing MCP runtime; do not create a second MCP client.

**Key capabilities:**
- Connect to external services (databases, APIs, file systems)
- Provide 10+ related tools from a single service
- Handle OAuth and complex authentication flows
- Bundle MCP servers with projects for automatic setup

## MCP Server Configuration

Configure servers in the `runtime.mcp_servers` list:

```yaml
runtime:
  mcp_servers:
    - name: database-tools
      transport: stdio
      command: [python, -m, my_mcp_server, --config, ./config.json]
      cwd: /absolute/project/path
      env:
        DB_URL: value-supplied-by-the-launch-environment
```

**Benefits:**
- Clear separation of concerns
- Easier to maintain
- Better for multiple servers

Use one list item per server. For HTTP/SSE/WebSocket, replace `command` with `url` and set `transport`; optional `headers`, `headers_helper`, `oauth`, `auth`, `cwd`, and `timeout_seconds` follow `MCPServerConfig`.

## MCP Server Types

### stdio (Local Process)

Execute local MCP servers as child processes. Best for local tools and custom servers.

**Configuration:**
```json
{
  "name": "filesystem",
  "transport": "stdio",
  "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"],
  "env": {
    "LOG_LEVEL": "debug"
  }
}
```

**Use cases:**
- File system access
- Local database connections
- Custom MCP servers
- NPM-packaged MCP servers

**Process management:**
- OwnPI spawns and manages the process
- Communicates via stdin/stdout
- Terminates when OwnPI exits

### SSE (Server-Sent Events)

Connect to hosted MCP servers with OAuth support. Best for cloud services.

**Configuration:**
```json
{
  "name": "asana",
  "transport": "sse",
  "url": "https://mcp.asana.com/sse"
}
```

**Use cases:**
- Official hosted MCP servers (Asana, GitHub, etc.)
- Cloud services with MCP endpoints
- OAuth-based authentication
- No local installation needed

**Authentication:**
- OAuth flows handled automatically
- User prompted on first use
- Tokens managed by OwnPI

### HTTP (REST API)

Connect to RESTful MCP servers with token authentication.

**Configuration:**
```json
{
  "name": "api-service",
  "transport": "http",
  "url": "https://api.example.com/mcp",
  "headers": {
    "X-Custom-Header": "value"
  }
}
```

**Use cases:**
- REST API-based MCP servers
- Token-based authentication
- Custom API backends
- Stateless interactions

### WebSocket (Real-time)

Connect to WebSocket MCP servers for real-time bidirectional communication.

**Configuration:**
```json
{
  "name": "realtime-service",
  "transport": "ws",
  "url": "wss://mcp.example.com/ws",
  "headers_helper": "/absolute/path/to/headers-helper"
}
```

**Use cases:**
- Real-time data streaming
- Persistent connections
- Push notifications from server
- Low-latency requirements

## Paths and Environment

Set `cwd` to the server project root and use a command list. The Runtime does not treat legacy project-root placeholders as configuration authority:
```json
{
  "command": ["./servers/my-server"],
  "cwd": "/absolute/project/path"
}
```

Pass non-secret server settings through `env`. Resolve secret values in the launch environment or a credential helper before configuration reaches traceable Runtime state:
```json
{
  "env": {
    "LOG_LEVEL": "info"
  }
}
```

Document required environment variables in the project README without recording their values.

## MCP Tool Naming

When MCP servers provide tools, they're automatically prefixed:

**Format:** `mcp__<server-name>__<tool-name>`

**Example:**
- Server: `asana`
- Tool: `create_task`
- **Full name:** `mcp__asana__asana_create_task`

### Using MCP Tools in Agent Loops

Discover the live server capability with `mcp_tool_list`, then invoke the exact tool through
`mcp_tool_invoke` or its loaded dynamic identity. Runtime permission policy is the authority;
do not add command-frontmatter grants or wildcard bypasses.

## Lifecycle Management

**Automatic startup:**
- MCP servers start under the configured Runtime lifecycle
- Connection established before first tool use
- Restart required for configuration changes

**Lifecycle:**
1. Runtime loads the user configuration
2. MCP configuration parsed
3. Server process started (stdio) or connection established (SSE/HTTP/WS)
4. Tools discovered and registered
5. Tools available as `mcp__...__...`

**Viewing servers:**
Use `mcp_tool_list` to inspect configured servers and discovered tools.

## Authentication Patterns

### OAuth (SSE/HTTP)

Start and inspect OAuth through `mcp_auth`:

```json
{
  "transport": "sse",
  "url": "https://mcp.example.com/sse"
}
```

User authenticates in browser on first use. No additional configuration needed.

### Token-Based (Headers)

Static headers are supported, but secrets should be resolved by `headers_helper` or the launch environment rather than committed inline:

```json
{
  "transport": "http",
  "url": "https://api.example.com",
  "headers_helper": "/absolute/path/to/headers-helper"
}
```

Document required environment variables in README.

### Environment Variables (stdio)

Pass configuration to MCP server:

```json
{
  "command": ["python", "-m", "my_mcp_server"],
  "env": {
    "LOG_LEVEL": "info"
  }
}
```

## Integration Patterns

### Pattern 1: Simple Tool Wrapper

Agent loops use MCP tools with user interaction:

```markdown
Steps:
1. Gather item details from user
2. Discover `name-server` with `mcp_tool_list`
3. Invoke `create_item` with `mcp_tool_invoke`
4. Confirm creation
```

**Use for:** Adding validation or preprocessing before MCP calls.

### Pattern 2: Autonomous Agent

Agents use MCP tools autonomously:

```markdown
# Agent: data-analyzer.md

Analysis Process:
1. Query data via mcp__db_server__query
2. Process and analyze results
3. Generate insights report
```

**Use for:** Multi-step MCP workflows without user interaction.

### Pattern 3: Multi-Server project

Integrate multiple MCP servers:

```json
{
  "github": {
    "transport": "sse",
    "url": "https://mcp.github.com/sse"
  },
  "jira": {
    "transport": "sse",
    "url": "https://mcp.jira.com/sse"
  }
}
```

**Use for:** Workflows spanning multiple services.

## Security Best Practices

### Use HTTPS/WSS

Always use secure connections:

```json
✅ "url": "https://mcp.example.com/sse"
❌ "url": "http://mcp.example.com/sse"
```

### Token Management

**DO:**
- ✅ Use environment variables for tokens
- ✅ Document required env vars in README
- ✅ Let OAuth flow handle authentication

**DON'T:**
- ❌ Hardcode tokens in configuration
- ❌ Commit tokens to git
- ❌ Share tokens in documentation

### Permission Scoping

Request only the exact MCP tool needed for the current operation. Do not use wildcard grants;
allow the Runtime permission evaluator to authorize each loaded dynamic identity.

## Error Handling

### Connection Failures

Handle MCP server unavailability:
- Provide fallback behavior in commands
- Inform user of connection issues
- Check server URL and configuration

### Tool Call Errors

Handle failed MCP operations:
- Validate inputs before calling MCP tools
- Provide clear error messages
- Check rate limiting and quotas

### Configuration Errors

Validate MCP configuration:
- Test server connectivity during development
- Validate JSON syntax
- Check required environment variables

## Performance Considerations

### Lazy Loading

MCP servers connect on-demand:
- Not all servers connect at startup
- First tool use triggers connection
- Connection pooling managed automatically

### Batching

Batch similar requests when possible:

```
# Good: Single query with filters
tasks = search_tasks(project="X", assignee="me", limit=50)

# Avoid: Many individual queries
for id in task_ids:
    task = get_task(id)
```

## Testing MCP Integration

### Local Testing

1. Configure MCP server in `Runtime YAML configuration`
2. Load project-local configuration from `.ownpi/`
3. Run `mcp_tool_list` to verify server appears
4. Test tool calls in commands
5. Check `Runtime trace and lifecycle inspection` logs for connection issues

### Validation Checklist

- [ ] MCP configuration is valid JSON
- [ ] Server URL is correct and accessible
- [ ] Required environment variables documented
- [ ] Tools appear in `mcp_tool_list` output
- [ ] Authentication works (OAuth or tokens)
- [ ] Tool calls succeed from commands
- [ ] Error cases handled gracefully

## Debugging

### Enable Debug Logging

Use Runtime status, lifecycle inspection, and trace logs.

Look for:
- MCP server connection attempts
- Tool discovery logs
- Authentication flows
- Tool call errors

### Common Issues

**Server not connecting:**
- Check URL is correct
- Verify server is running (stdio)
- Check network connectivity
- Review authentication configuration

**Tools not available:**
- Verify server connected successfully
- Check tool names match exactly
- Run `mcp_tool_list` to see available tools
- Restart OwnPI after config changes

**Authentication failing:**
- Clear cached auth tokens
- Re-authenticate
- Check token scopes and permissions
- Verify environment variables set

## Quick Reference

### MCP Server Types

| Type | Transport | Best For | Auth |
|------|-----------|----------|------|
| stdio | Process | Local tools, custom servers | Env vars |
| SSE | HTTP | Hosted services, cloud APIs | OAuth |
| HTTP | REST | API backends, token auth | Tokens |
| ws | WebSocket | Real-time, streaming | Tokens |

### Configuration Checklist

- [ ] Server type specified (stdio/SSE/HTTP/ws)
- [ ] Type-specific fields complete (command or url)
- [ ] Authentication configured
- [ ] Environment variables documented
- [ ] HTTPS/WSS used (not HTTP/WS)
- [ ] . used for paths

### Best Practices

**DO:**
- ✅ Use . for portable paths
- ✅ Document required environment variables
- ✅ Use secure connections (HTTPS/WSS)
- ✅ Request only the specific MCP tools required by the workflow
- ✅ Test MCP integration before publishing
- ✅ Handle connection and tool errors gracefully

**DON'T:**
- ❌ Hardcode absolute paths
- ❌ Commit credentials to git
- ❌ Use HTTP instead of HTTPS
- ❌ Request wildcard Tool grants
- ❌ Skip error handling
- ❌ Forget to document setup

## Additional Resources

### Reference Files

For detailed information, consult:

- **`references/server-types.md`** - Deep dive on each server type
- **`references/authentication.md`** - Authentication patterns and OAuth
- **`references/tool-usage.md`** - Using MCP tools in commands and agents

### Example Configurations

Working examples in `examples/`:

- **`stdio-server.json`** - Local stdio MCP server
- **`sse-server.json`** - Hosted SSE server with OAuth
- **`http-server.json`** - REST API with token auth

### External Resources

- **Official MCP Docs**: https://modelcontextprotocol.io/
- **Model Context Protocol**: https://modelcontextprotocol.io/
- **MCP SDK**: @modelcontextprotocol/sdk
- **Testing**: Use `Runtime trace and lifecycle inspection` and `mcp_tool_list` command

## Implementation Workflow

To add MCP integration to a project:

1. Choose MCP server type (stdio, SSE, HTTP, ws)
2. Add list entries under `runtime.mcp_servers`
3. Use explicit `cwd` and tokenized stdio commands
4. Document required environment variables in README
5. Test locally with `mcp_tool_list`
6. Grant only the required MCP tools through Runtime permission policy
7. Handle authentication (OAuth or tokens)
8. Test error cases (connection failures, auth errors)
9. Document MCP integration in project README

Focus on stdio for custom/local servers, SSE for hosted services with OAuth.
