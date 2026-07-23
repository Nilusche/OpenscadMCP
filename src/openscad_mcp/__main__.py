"""Console entry point for the OpenSCAD MCP server."""

from __future__ import annotations

from .server import mcp


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
