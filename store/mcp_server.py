"""An MCP server generated from the API's OpenAPI spec.

Nothing here is written per route. FastMCP makes one tool for each route the
rules below let through: the route's operation_id becomes the tool's name and
its docstring the description.

Run as a server:  uv run python -m store.mcp_server   (stdin and stdout by default)
"""

from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

from store.api import INVESTIGATE, app

# The rules for turning API routes into MCP tools. They are tried top to
# bottom and the first that matches a route wins.
READ_ONLY = [
    # A route becomes a tool only if it is a GET and carries the "investigate"
    # tag (see api.py). The other GET routes serve facts that code fetches for
    # every run, so a model never needs to choose them.
    RouteMap(methods=["GET"], tags=set(INVESTIGATE), mcp_type=MCPType.TOOL),
    # Anything else is left out. Without this line FastMCP's own last rule
    # would apply, and that rule turns every remaining route into a tool,
    # writes included.
    RouteMap(mcp_type=MCPType.EXCLUDE),
]


def build_server(api_app=app):
    """Generate the MCP server. A tool call reaches the API as an HTTP request
    handled inside this same program: no port and no network."""
    return FastMCP.from_fastapi(app=api_app, name="triage-store", route_maps=READ_ONLY)


mcp = build_server()

if __name__ == "__main__":
    mcp.run()
