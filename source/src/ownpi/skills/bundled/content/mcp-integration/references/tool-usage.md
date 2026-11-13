# Using MCP Tools in Commands and Agents

Complete guide to using MCP tools effectively in OwnPI agent loops.

> OwnPI adapter: discover tools with `mcp_tool_list`, invoke them with `mcp_tool_invoke`, and use dynamic identities `mcp__<server_name>__<tool_name>`. Runtime permission policy is the only permission authority. Use `mcp_resource_list`, `mcp_resource_read`, and `mcp_auth` for resources and authentication.

## Overview

Once an MCP server is configured, its tools become available with the prefix `mcp__<server-name>__<tool-name>`. Use these tools in commands and agents just like built-in OwnPI tools.

## Tool Naming Convention

### Format

```
mcp__<server-name>__<tool-name>
```

### Examples

**Asana project with asana server:**
- `mcp__asana__asana_create_task`
- `mcp__asana__asana_search_tasks`
- `mcp__asana__asana_get_project`

**Custom project with database server:**
- `mcp__database__query`
- `mcp__database__execute`
- `mcp__database__list_tables`

### Discovering Tool Names

Invoke `mcp_tool_list` with an optional `server_name` filter. Use the returned canonical tool
name and schema with `mcp_tool_invoke`.

This shows:
- All available MCP servers
- Tools provided by each server
- Tool schemas and descriptions
- Full tool names for use in configuration

## Using Tools in Agent Loops

### Permission Scope

Request the exact discovered MCP tool needed by the current operation. The Runtime permission
evaluator decides whether the dynamic identity can execute; skill or command text does not grant
permission by itself.

### Multiple Tools

Discover once with `mcp_tool_list`, then request exact dynamic identities one at a time as the
workflow needs them. Do not use wildcard permission grants.

### Asana Workflow Example

1. Call `mcp_tool_list` for the configured Asana server.
2. Select the exact search tool and invoke it with explicit filters.
3. Validate the returned task identity before requesting a mutation tool.
4. Invoke the exact create or comment tool through `mcp_tool_invoke`.
5. Report the canonical result or the Runtime permission/auth diagnostic.

## Tool Call Patterns

### Pattern 1: Simple Tool Call

Single tool call with validation:

```markdown
Steps:
1. Validate user provided required fields
2. Call mcp__api_server__create_item with validated data
3. Check for errors
4. Display confirmation
```

### Pattern 2: Sequential Tools

Chain multiple tool calls:

```markdown
Steps:
1. Search for existing items: mcp__api_server__search
2. If not found, create new: mcp__api_server__create
3. Add metadata: mcp__api_server__update_metadata
4. Return final item ID
```

### Pattern 3: Batch Operations

Multiple calls with same tool:

```markdown
Steps:
1. Get list of items to process
2. For each item:
   - Call mcp__api_server__update_item
   - Track success/failure
3. Report results summary
```

### Pattern 4: Error Handling

Graceful error handling:

```markdown
Steps:
1. Try to call mcp__api_server__get_data
2. If error (rate limit, network, etc.):
   - Wait and retry (max 3 attempts)
   - If still failing, inform user
   - Suggest checking configuration
3. On success, process data
```

## Tool Parameters

### Understanding Tool Schemas

Each MCP tool has a schema defining its parameters. View with `mcp_tool_list`.

**Example schema:**
```json
{
  "name": "asana_create_task",
  "description": "Create a new Asana task",
  "inputSchema": {
    "transport": "object",
    "properties": {
      "name": {
        "transport": "string",
        "description": "Task title"
      },
      "notes": {
        "transport": "string",
        "description": "Task description"
      },
      "workspace": {
        "transport": "string",
        "description": "Workspace GID"
      }
    },
    "required": ["name", "workspace"]
  }
}
```

### Calling Tools with Parameters

the agent automatically structures tool calls based on schema:

```typescript
// the agent generates this internally
{
  toolName: "mcp__asana__asana_create_task",
  input: {
    name: "Review PR #123",
    notes: "Code review for new feature",
    workspace: "12345",
    assignee: "67890",
    due_on: "2025-01-15"
  }
}
```

### Parameter Validation

**In commands, validate before calling:**

```markdown
Steps:
1. Check required parameters:
   - Title is not empty
   - Workspace ID is provided
   - Due date is valid format (YYYY-MM-DD)
2. If validation fails, ask user to provide missing data
3. If validation passes, call MCP tool
4. Handle tool errors gracefully
```

## Response Handling

### Success Responses

```markdown
Steps:
1. Call MCP tool
2. On success:
   - Extract relevant data from response
   - Format for user display
   - Provide confirmation message
   - Include relevant links or IDs
```

### Error Responses

```markdown
Steps:
1. Call MCP tool
2. On error:
   - Check error type (auth, rate limit, validation, etc.)
   - Provide helpful error message
   - Suggest remediation steps
   - Don't expose internal error details to user
```

### Partial Success

```markdown
Steps:
1. Batch operation with multiple MCP calls
2. Track successes and failures separately
3. Report summary:
   - "Successfully processed 8 of 10 items"
   - "Failed items: [item1, item2] due to [reason]"
   - Suggest retry or manual intervention
```

## Performance Optimization

### Batching Requests

**Good: Single query with filters**
```markdown
Steps:
1. Call mcp__api_server__search with filters:
   - project_id: "123"
   - status: "active"
   - limit: 100
2. Process all results
```

**Avoid: Many individual queries**
```markdown
Steps:
1. For each item ID:
   - Call mcp__api_server__get_item
   - Process item
```

### Caching Results

```markdown
Steps:
1. Call expensive MCP operation: mcp__api_server__analyze
2. Store results in variable for reuse
3. Use cached results for subsequent operations
4. Only re-fetch if data changes
```

### Parallel Tool Calls

When tools don't depend on each other, call in parallel:

```markdown
Steps:
1. Make parallel calls (the agent handles this automatically):
   - mcp__api_server__get_project
   - mcp__api_server__get_users
   - mcp__api_server__get_tags
2. Wait for all to complete
3. Combine results
```

## Integration Best Practices

### User Experience

**Provide feedback:**
```markdown
Steps:
1. Inform user: "Searching Asana tasks..."
2. Call mcp__asana__asana_search_tasks
3. Show progress: "Found 15 tasks, analyzing..."
4. Present results
```

**Handle long operations:**
```markdown
Steps:
1. Warn user: "This may take a minute..."
2. Break into smaller steps with updates
3. Show incremental progress
4. Final summary when complete
```

### Error Messages

**Good error messages:**
```
❌ "Could not create task. Please check:
   1. You're logged into Asana
   2. You have access to workspace 'Engineering'
   3. The project 'Q1 Goals' exists"
```

**Poor error messages:**
```
❌ "Error: MCP tool returned 403"
```

### Documentation

**Document MCP tool usage in command:**
```markdown
## MCP Tools Used

This command uses the following Asana MCP tools:
- **asana_search_tasks**: Search for tasks matching criteria
- **asana_create_task**: Create new task with details
- **asana_update_task**: Update existing task properties

Ensure you're authenticated to Asana before running this command.
```

## Testing Tool Usage

### Local Testing

1. **Configure MCP server** in `Runtime YAML configuration`
2. **Load project configuration** from `.ownpi/`
3. **Verify tools available** with `mcp_tool_list`
4. **Test command** that uses tools
5. **Check debug output**: `Runtime trace and lifecycle inspection`

### Test Scenarios

**Test successful calls:**
```markdown
Steps:
1. Create test data in external service
2. Run command that queries this data
3. Verify correct results returned
```

**Test error cases:**
```markdown
Steps:
1. Test with missing authentication
2. Test with invalid parameters
3. Test with non-existent resources
4. Verify graceful error handling
```

**Test edge cases:**
```markdown
Steps:
1. Test with empty results
2. Test with maximum results
3. Test with special characters
4. Test with concurrent access
```

## Common Patterns

### Pattern: CRUD Operations

```markdown
# Item Management

## Create
Discover and invoke create_item with required fields...

## Read
Use read_item with item ID...

## Update
Use update_item with item ID and changes...

## Delete
Request delete_item with item ID only after confirmation and Runtime permission approval...
```

### Pattern: Search and Process

```markdown
Steps:
1. **Search**: mcp__api_server__search with filters
2. **Filter**: Apply additional local filtering if needed
3. **Transform**: Process each result
4. **Present**: Format and display to user
```

### Pattern: Multi-Step Workflow

```markdown
Steps:
1. **Setup**: Gather all required information
2. **Validate**: Check data completeness
3. **Execute**: Chain of MCP tool calls:
   - Create parent resource
   - Create child resources
   - Link resources together
   - Add metadata
4. **Verify**: Confirm all steps succeeded
5. **Report**: Provide summary to user
```

## Troubleshooting

### Tools Not Available

**Check:**
- MCP server configured correctly
- Server connected (check `mcp_tool_list`)
- Tool names match exactly (case-sensitive)
- Restart OwnPI after config changes

### Tool Calls Failing

**Check:**
- Authentication is valid
- Parameters match tool schema
- Required parameters provided
- Check `Runtime trace and lifecycle inspection` logs

### Performance Issues

**Check:**
- Batching queries instead of individual calls
- Caching results when appropriate
- Not making unnecessary tool calls
- Parallel calls when possible

## Conclusion

Effective MCP tool usage requires:
1. **Understanding tool schemas** via `mcp_tool_list`
2. **Requesting exact tools** through Runtime permission policy
3. **Handling errors gracefully**
4. **Optimizing performance** with batching and caching
5. **Providing good UX** with feedback and clear errors
6. **Testing thoroughly** before deployment

Follow these patterns for robust MCP tool integration in your project commands and agents.
