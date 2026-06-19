---
name: codex-session-audit
description: Parse and audit Codex rollout/session JSONL files. Use when the user asks whether Codex really called tools such as shell_command, web.run, image/file tools, MCP tools, or browser tools; wants to distinguish prompt mentions from real response_item function_call execution; wants local image/input summaries; or wants to inspect visible assistant messages, encrypted reasoning boundaries, final outputs, and tool-impact evidence from large Codex logs.
---

# Codex Session Audit

Use this skill for Codex `rollout-*.jsonl` or session JSONL files under
`.codex/sessions/...`. Codex logs often include huge `session_meta` records with
base instructions, user prompts, project context, and embedded artifact text.
Do not use raw grep as evidence of tool execution: prompts and system
instructions often contain tool names, commands, examples, or previous outputs.

## Path Handling

- Accept exact JSONL paths from the user.
- When referring to common locations, write them generically, e.g.
  `%USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-....jsonl`.
- Do not hard-code personal absolute paths in new docs, skills, or summaries
  unless the user supplied the path in the current task.

## Workflow

1. Identify session files:
   - If the user gives exact files, audit only those files.
   - If the user gives a directory, inspect `rollout-*.jsonl` and `*.jsonl`.
   - For multiple files, list size and modified time before deep analysis.
2. Parse JSONL structurally:
   - Read line by line with UTF-8 and `errors="replace"`.
   - `json.loads` each non-empty line.
   - Do not rely on shell text filters for evidence; `session_meta` and prompts
     can be extremely large and noisy.
3. Separate prompt mentions from real tool calls:
   - Prompt mentions live in user/display strings, `base_instructions`, and
     environment context.
   - Real Codex tool calls appear as `type="response_item"` with
     `payload.type="function_call"`.
   - Tool results appear as `payload.type="function_call_output"` linked by
     `call_id`.
4. Summarize inputs and outputs:
   - `session_meta.payload.id`, `cwd`, `originator`, `model_provider`;
   - `turn_context.payload.model`, approval/sandbox settings;
   - user prompt snippets, assistant status/final messages;
   - `local_images` / `images` counts and representative paths;
   - tool name, call line, arguments, output status/snippet;
   - final artifact-like output, SRT/JSON indicators, and modified-file claims.
5. For reasoning/debug questions:
   - Report that `response_item.reasoning.encrypted_content` is not readable.
   - Use visible `reasoning.summary` only when present and non-empty.
   - Inspect assistant status messages and tool calls around the relevant point.
   - Compare final outputs and project artifacts before claiming impact.

## Parser Script

Prefer the bundled parser for first-pass audits:

```powershell
python ".agents\skills\codex-session-audit\scripts\audit_codex_session.py" <path> --keywords shell_command,get_frames,pre_pass,chunk,refine
```

Useful options:

- `--json` emits machine-readable summaries.
- `--show-prompts` includes prompt mention snippets.
- `--show-messages` includes visible assistant message snippets.
- `--keywords a,b,c` controls which strings are highlighted in prompts,
  messages, tool args/results, and final content.

The parser is intentionally conservative: command or tool names in prompt text
are reported separately from actual `function_call` records.

On Windows, console encodings such as `cp950` may fail on Japanese text while
printing summaries. The bundled parser configures UTF-8 output; if an ad hoc
script fails with `UnicodeEncodeError`, rerun with this parser or set
`PYTHONUTF8=1`.

## Interpretation Rules

- A string such as `shell_command`, `get_frames.py`, or `web.run` in
  `session_meta` or a user prompt does not prove execution.
- A `response_item` with `payload.type="function_call"` proves Codex requested a
  tool call. A matching `function_call_output` proves the tool returned output.
- `local_images` proves image files were attached to the model input; it is not
  a tool call by itself.
- Empty or missing `reasoning.summary` means the internal reasoning is not
  inspectable; do not infer private chain-of-thought from encrypted content.
- To judge whether a tool affected a result, compare:
  - the tool command/query and output;
  - visible assistant messages before/after the call;
  - final output or written artifact differences;
  - project files such as `.pre_pass`, `.chunks`, `.refine`, or generated SRTs
    when the user asks about translation pipeline impact.
