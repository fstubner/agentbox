"""One module per integration: TOOLS plus a dispatch(name, args).

Each module is the assistant-facing definition of one bridge's surface — the
designed grammar, not a wrapper. The gateway assembles them into a single tool
registry and refuses to start on a name collision.
"""
