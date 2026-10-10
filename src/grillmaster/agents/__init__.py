"""Agent orchestration: one task description, one event stream, four CLIs.

`AgentRunner` runs an `AgentTask` on the backend its role maps to (agy,
gemini, codex or Claude Code), streams normalized activity to the event bus, repairs
invalid output by resuming the same session, and retries only transient
failures. Imports only `core`, `events` and third-party code.
"""
