"""MCP stdio server exposing grill's agent tools (`get_frames`, `check_srt`).

Launched per agent call as `python -m grillmaster.agent_tools --session
<tools.json>`; the manifest (`core.tool_session`) decides which tools exist
and scopes them. Validation failures surface as MCP tool errors the agent
can read and correct.
"""
