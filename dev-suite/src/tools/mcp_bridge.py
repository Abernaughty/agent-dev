"""Bridge between LangGraph agents and MCP servers.

Wraps MCP server calls as LangChain tools so agents
can use filesystem and GitHub operations naturally.

Implementation in Step 5.
"""

# TODO Step 5:
# - Load MCP config from mcp-config.json
# - Implement filesystem_read(), filesystem_write(), filesystem_list()
# - Implement github_create_pr(), github_read_diff()
# - Wrap as LangChain Tool objects for agent use
# - Validate MCP server versions match pinned config
