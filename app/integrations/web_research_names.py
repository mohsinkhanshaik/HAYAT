"""The built-in web-research MCP server's identity, without importing the server.

The gateway registers this server at every start, and importing
web_research_mcp for these two names pulled in the whole MCP SDK and the
research stack (about 0.9 s of startup) before anything used them.
"""
SERVER_ID = "zero-cost-web-research"
READ_ONLY_TOOLS = [
    "search_web", "deep_web_research", "fetch_public_page",
    "inspect_google_snapshot", "google_search_status",
]
