# Google Workspace MCP Lite

Local MCP wrapper for the Google Workspace bridge.

Tools:

- `search_gmail`
- `read_gmail`
- `search_grocer_orders`
- `extract_grocer_order`
- `list_gmail_labels`
- `create_gmail_label`
- `mark_gmail_read`
- `archive_gmail`
- `add_gmail_labels`
- `list_calendars`
- `list_calendar_events`
- `calendar_freebusy`
- `create_agent_calendar_event`

The wrapper exposes only the bridge's narrowed operations. It does not expose
raw OAuth tokens, Google client secrets, Gmail send, or Gmail delete.
