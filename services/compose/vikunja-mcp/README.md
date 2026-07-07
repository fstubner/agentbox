# Vikunja MCP

Docker-hosted Streamable HTTP MCP server for private task/project management.

This service wraps `vikunja-bridge` and exposes a small MCP tool set for Hermes:

- `list_projects`
- `find_or_create_project`
- `list_tasks`
- `create_task`
- `update_task`
- `complete_task`
- `add_task_comment`
- `mark_cleanup_candidate`
- `cleanup_report`

## Exposure

- Host bind: `127.0.0.1:3467`
- MCP endpoint: `http://127.0.0.1:3467/mcp`
- Health endpoint: `http://127.0.0.1:3467/health`

Do not expose this service to LAN or Tailnet unless explicitly approved.

## Secrets

Create:

```text
~/.config/agent-control-plane/vikunja-mcp.env
```

Recommended values:

```bash
VIKUNJA_BRIDGE_URL=http://vikunja-bridge:8080
VIKUNJA_BRIDGE_TOKEN=op://Agentbox/vikunja-bridge/bridge_token
LAN_BIND_IP=127.0.0.1
```

`VIKUNJA_MCP_SHARED_TOKEN` is optional while bound to localhost. Add it later if another local client besides Hermes will access this MCP endpoint.

## Hermes Config

```yaml
mcp_servers:
  vikunja_tasks:
    url: "http://127.0.0.1:3467/mcp"
    timeout: 30
    connect_timeout: 10
    supports_parallel_tool_calls: false
    tools:
      include:
        - list_projects
        - find_or_create_project
        - list_tasks
        - create_task
        - update_task
        - complete_task
        - add_task_comment
        - mark_cleanup_candidate
        - cleanup_report
      prompts: false
      resources: false
```

## Sources

- MCP Streamable HTTP transport: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP tools spec: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- Hermes MCP docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
