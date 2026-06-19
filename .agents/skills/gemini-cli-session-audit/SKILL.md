---
name: gemini-cli-session-audit
description: Parse and audit Gemini CLI session JSONL files and tmp session directories. Use when the user asks whether Gemini CLI really called tools such as get_frames, run_shell_command, read_file, or google_web_search; wants to distinguish prompt mentions from real tool execution; wants command/timestamp/image/search summaries; or wants to inspect thoughts/final output from large Gemini CLI chat logs safely.
---

# Gemini CLI Session Audit

Use this skill for Gemini CLI `chats/session-*.jsonl` files or `gemini-cli-*`
tmp directories. Gemini logs can contain very long JSONL records with embedded
prompts, base64 images, and tool outputs. Do not use raw grep as evidence of
tool execution: prompt text often contains command snippets.

## Path Handling

- Accept exact paths from the user.
- When referring to common locations, write them generically, e.g.
  `%USERPROFILE%\.gemini\tmp\gemini-cli-...\chats\session-....jsonl`.
- Do not hard-code or repeat personal absolute paths in new docs, skills, or
  summaries unless the user supplied the path in the current task.

## Workflow

1. Identify session files:
   - If the user gives a `gemini-cli-*` directory, inspect `chats/session-*.jsonl`.
   - If multiple files exist, list them with size and modified time first.
2. Parse JSONL structurally:
   - Read line by line with UTF-8 and `errors="replace"`.
   - `json.loads` each non-empty line.
   - Handle very long records; do not load via shell text filters.
3. Separate prompt mentions from real tool calls:
   - Prompt mentions live in user/display content strings.
   - Real calls usually appear in `toolCalls` arrays attached to Gemini records.
   - Treat `tokens.tool = 0` plus no `toolCalls` as strong evidence that no
     tool was actually invoked.
4. For tool-use questions, summarize:
   - tool name, line/record number, command/query/file path, status/result;
   - extracted frame timestamps and counts;
   - read image paths and whether reads succeeded;
   - web search queries and whether final output used the result;
   - whether a raw SRT or JSON final answer was produced.
5. For reasoning/debug questions, inspect nearby thoughts:
   - Pull only concise thought summaries around the relevant tool call lines.
   - Look for why the model searched, fetched frames, read images, or stopped.
   - Compare final output/cache artifacts when available before claiming impact.

## Parser Script

Prefer the bundled parser for first-pass audits:

```powershell
python ".agents\skills\gemini-cli-session-audit\scripts\audit_gemini_session.py" <path> --keywords get_frames_for_chunk.py,extra_frames,google_web_search
```

Useful options:

- `--json` emits machine-readable summaries.
- `--show-thoughts` includes relevant Gemini thought summaries.
- `--keywords a,b,c` controls which strings are highlighted in prompts,
  thoughts, tool args/results, and final content.

The parser is intentionally conservative: command mentions in prompt text are
reported separately from actual `toolCalls`.

On Windows, console encodings such as `cp950` may fail on Japanese text while
printing summaries. The bundled parser configures UTF-8 output; if an ad hoc
script fails with `UnicodeEncodeError`, rerun with this parser or set
`PYTHONUTF8=1`.

## Interpretation Rules

- `get_frames_for_chunk.py` in the prompt does not prove execution.
- A successful `run_shell_command` tool call containing `get_frames_for_*` plus
  output paths under `extra_frames` proves frame extraction.
- A later `read_file` call for those frame paths proves the model opened the
  images.
- If a session has searches but no final SRT/JSON answer, report it as
  unfinished or inconclusive unless another artifact proves completion.
- To judge whether a tool helped, compare:
  - the tool call's stated reason/description;
  - nearby thoughts after the result;
  - final output lines around the relevant timestamps or terms;
  - project cache files such as `.chunks/responses/chunk_*.raw.srt` when the
    user asks about pipeline completion.
