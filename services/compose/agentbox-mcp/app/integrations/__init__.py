"""One module per integration, each with TOOLS and dispatch(name, args).

Each module defines what the assistant can do with one bridge, designed rather
than wrapped. The server combines them into one registry and refuses to start
if two tools share a name.
"""
