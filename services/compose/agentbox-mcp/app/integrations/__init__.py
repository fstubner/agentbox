"""One module per integration, each with TOOLS and dispatch(name, args).

Each module defines what the assistant can do with one bridge. The tools are
written for the assistant and are not a direct wrapper of the bridge API. The
server combines them into one registry and refuses to start
if two tools share a name.
"""
