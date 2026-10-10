---
name: agent-orchestration
description: >-
  The agent layer `src/grillmaster/agents/` (AgentTask/AgentJob, AgentRunner,
  strict schema, repair loop, errors, events, `process.py`,
  agy/gemini/codex/claude adapters) and the MCP tool server `src/grillmaster/agent_tools/`
  (`get_frames`, `check_srt`, `core/tool_session.py`). Read this before
  touching either package, adding a backend, capability or tool, or changing
  prompts, media, schema, repair, retries, concurrency or session records.
---

# Agent orchestration (`agents/` + `agent_tools/`)

## Invariants

- Every agent call goes through `AgentRunner` (built in
  `pipeline/runner.py`, exposed as `StageContext.agents`); domain code builds
  an `AgentTask` and never touches an adapter or CLI.
- Child processes are spawned only by `agents/process.py` (agent CLIs) and
  `media/ffmpeg.py`, both as `core.process.spawn_tree` `ProcessTree`s
  (Windows Job Object / POSIX process group, killable after the leader
  exited) held via `track`/`release` in `LIVE_PROCESSES`. The Claude SDK's
  CLI is `adopt_tree`d by `RegisteredTransport` after `connect` (Job Object;
  failure → `AgentConfigError`) and its tree always ends on `close`. Abort
  (TUI/CLI) and `atexit` call `kill_all()`: it latches the process-wide
  `core.process.ABORT` (new spawns then raise `ProcessAbortedError`), then
  kills every tree.
- `agents` and `agent_tools` never import `config`/`project` (import-linter
  `domain-purity`); `ModelSpec`, `Role` and `ToolSession` live in `core/`.
- No silent degradation: missing capability/model/input file or a non-strict
  schema raises `AgentConfigError` before any slot is taken.
- Workspace isolation: agy, gemini and codex load every `AGENTS.md`/`GEMINI.md`
  and `.agents/` skill from their cwd (and agy's `--add-dir`s) up to the
  nearest `.git`. `workspace.seal_workspace_root` puts an empty `.git` file at the
  root holding all agent workdirs and inputs (the pipeline seals
  `projects/`), so a surrounding checkout's dev rules never reach a task.
- A thin layer of our own over the four CLIs, not ACP or an agent framework:
  agy has no native ACP and ACP has no structured output. The event vocabulary
  borrows ACP's.

## Task API (`task.py`, `schema.py`)

- `AgentTask[T]`: `name` (e.g. `chunks/0001-0119`), `role`, `instructions` +
  `prompt` (sent as ONE user message on every backend), `session_dir`,
  `workdir` (cwd and writable root; `None` = temp dir; concurrent tasks may not
  share one), `output`, `images`, `audio`, `tools`, `add_dirs`, `validate`
  (raises `ValidationFailure`), `requires`, `max_repairs=3`, `attempts=1`.
- Outputs: `TextOutput`, `SchemaOutput(model)` (see `schema_delivery`),
  `FilesOutput(required, optional)` (both deleted before each attempt). A
  failed parse is a `ValidationFailure`, i.e. a repair round.
- `run(task) -> AgentResult` or a classified `AgentError`. Fan-out uses
  `run_jobs(jobs) -> list[JobFailure]` with `AgentJob(name, prepare, accept)`:
  `prepare` builds the task on the worker before it takes a slot, `accept`
  persists the result right after its session. An exception fails only its
  job; failures return in input order and the caller raises after the batch.
  Ctrl-C cancels the runner and unstarted jobs; running ones end without a
  new turn and accept what succeeded. After an `AgentQuotaError`, jobs of that
  backend that have not entered `run` fail at once with `AgentQuotaError`;
  other backends' jobs continue.
- `strict_json_schema`: refs inlined, all properties required,
  `additionalProperties: false`, object root; free-key dicts and recursive
  models are rejected (use `T | None`, lists of objects). Pydantic is the judge.

## Roles and capabilities

`grill.toml [agents.roles]` → `AgentRoles.specs()` (`chat` falls back to
`utility`) → `dict[Role, ModelSpec]`, written `backend/model[/effort]`
(default `high`). `[agents] max_concurrent` (default 10) sizes the semaphore
of one grill process (concurrent processes each get their own);
`timeout_minutes` bounds each turn (process watchdog; `asyncio.timeout` for
Claude), not the session.

All adapters declare IMAGE_INPUT, SCHEMA_OUTPUT, RESUME, MCP, WEB_SEARCH; agy
and gemini have AUDIO_INPUT, only codex IMAGE_GENERATION. Required =
`task.requires` (what inputs cannot imply: IMAGE_GENERATION for cover,
WEB_SEARCH for prepass/titles/date research) + derived: schema→SCHEMA_OUTPUT,
`max_repairs > 0`→RESUME, images→IMAGE_INPUT, audio→AUDIO_INPUT, tools→MCP,
frames tool on a `NEXT_MESSAGE` backend→RESUME + IMAGE_INPUT.
Stages check `runner.capabilities(role)` (`accepts_audio`) before adding audio.

## Adapters (`adapters/`, loaded lazily by `AdapterRegistry`)

`base.py`: `media_delivery` (`ATTACHED`, or `VIEW_FILE`: the runner lists
absolute paths to open in one parallel turn), `tool_image_delivery` (frames
tool images: `INLINE`, `VIEW_FILE`, or `NEXT_MESSAGE`: the runner writes
`tools.json` with `frames.pending_frames` = `<session>/frames_pending.txt`,
`get_frames` returns text and appends paths there (`core.tool_session`
`leave_`/`take_pending_frames`), and the runner resumes with
`prompt.frames_message()` + those images; not a repair, at most
`MAX_FRAME_TURNS`, counted as `frame_turns` in `result.json`), `schema_delivery` (`NATIVE`; `FINISH_TOOL`: the runner
tells the model to submit through `finish`, agy otherwise prints the JSON and
its own nag costs a full-context turn; `PROMPT`: the runner states the schema
and reads the answer from `FinalOutput.text` with
`schema.json_object_answer(lead_in=True)`), `preflight`, `start`/`resume(session_id, TurnRequest)` → `SessionHandle` →
`FinalOutput(session_id, text, structured, usage, defects)`. Closing
`events()` early stops the turn (the CLI tree is killed). A resume repeats
model, workdir, tools and schema with a new `message`; its images/audio are
only a defect's resends.

- **agy**: `--input-format stream-json --output-format stream-json -p=`, one
  text-only stdin line, stdin closed on `result`. Model id
  `<model>-<low|medium|high>` checked against `agy models` (success cached
  for good; a failure answers every task for 60 s). Audio not
  opened by a finished `view_file` step → `TurnDefect` resending it.
  `--json-schema` output trusted only if `finish` ran this turn, else a JSON
  object in this turn's final message (one json fence stripped). MCP from
  `<workdir>/.agents/mcp_config.json` (deleted when no tools). Resume
  `--conversation`. `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_API_KEY`
  are stripped so agy stays on the subscription login (its only isolation
  from user settings). Repair wording must never forbid tool calls: `finish`
  is a tool. MCP image results reach the model only as offloaded files it
  must `view_file` (one more turn), so the prompt says so.
- **gemini** (gemini-cli; Code Assist login, needs `GOOGLE_CLOUD_PROJECT`):
  `--output-format stream-json --skip-trust --approval-mode yolo
  --allowed-mcp-server-names grill --prompt=`, message on stdin. Input media
  go as quoted `@"<abs path>"` lines appended to the message plus
  `--include-directories` (the token cost of agy's `view_file` turns is the
  reason this backend exists); `preflight` refuses files over 20 MB. The CLI
  parses every `@` (files, subagents, MCP resources; respacing the text once
  a file attaches), so the message's own `@` are sent as U+FF20. Image tool results are broken through Code Assist (400 on
  3.5/3.8-flash, garbled text after the image on 3.1-pro/flash-lite, the
  nested format drops the image), hence `NEXT_MESSAGE`; `SchemaDelivery.PROMPT`.
  agy and gemini share `_google.py` (key stripping, `mcpServers` entry,
  readable roots). Effort → `thinkingLevel` (`thinkingBudget` for
  `gemini-2.5*`), the MCP server and `model.compressionThreshold: 1000` go
  into `<workdir>/.gemini/settings.json` (rewritten every turn). Without the
  last one a resumed turn estimates audio by base64 length, passes the 0.5
  default and compresses the attached audio out of the history (prepass then
  ended `GRILL_AUDIO_UNAVAILABLE`). Model ids are gemini-cli's (`gemini-3.1-pro-preview`,
  not agy's `gemini-3.1-pro`); with media attached, an unknown id or an
  oversized request ends in a `success` result with empty `stats.models` (no
  model call, no saved session), which the parser raises as `AgentConfigError`.
  Resume `--resume <id>` (sessions are per cwd). Same
  API-key stripping as agy; the user's `~/.gemini` settings and `GEMINI.md`
  still load.
- **codex**: `codex exec --json … -`, `--ignore-user-config`, sandbox bypassed,
  `--image` on the start turn only, `--output-schema` (last `agent_message`),
  `-c mcp_servers.grill.*`, `-c tools.web_search=true` only when required.
  `exec resume <thread>` has no `--cd`: the process cwd is the workdir. No
  `--ephemeral` (resume needs the stored thread), so sessions accumulate in
  `~/.codex/sessions`. `--ignore-user-config` skips only `config.toml`: the
  user's `~/.codex/AGENTS.md` still loads (no switch short of `CODEX_HOME`).
- **claude**: SDK `query()` under `asyncio.run` on a per-turn thread (the only
  module allowed asyncio); `setting_sources=[]` + `strict_mcp_config=True`
  (like codex's `--ignore-user-config`: the user's own settings, MCP servers
  and CLAUDE.md never reach a grill session), no `system_prompt` (the SDK
  then sends an empty one), base64 image blocks, `output_format`, `resume=`.
  Stream-captured rate-limit and error details win over the SDK's opaque
  exception.

## Runner loop (`runner.py`)

- Each attempt gets a fresh session dir (`session/` or `session_<label>/`,
  retries `session.2/`, …; names kept short for MAX_PATH): `prompt.md`,
  `tools.json` (with tools), `schema.json` (with a schema), `raw.jsonl`
  (verbatim CLI stream; encoded SDK messages for Claude), `result.json`
  (`SessionRecord`); agy adds `agy.log`. Normalized events go only to the
  project's `logs/events-<ts>.jsonl`, as `AgentActivity` summaries.
- One global slot is held from the first turn through every repair. Pending
  frames (`NEXT_MESSAGE`) first, then adapter defects, then parse +
  `validate`; a failure resumes the same session
  with only `prompt.repair_message(...)`, so the agent keeps its context
  (heard audio, fetched frames) and the prompt is not resent. Exhausted
  repairs → `AgentOutputError`. Nested `run`/`run_jobs` inside a slot is
  refused (deadlock). `run_jobs` polls with a timed wait because lock waits
  are not interruptible on Windows.
- Errors: `AgentConfigError`, `AgentQuotaError` (quota/usage-limit phrase,
  any status), `AgentAuthError` (401 + login hint), `AgentTransientError`
  (timeout, crash, no result, a bare 429/rate limit), `AgentOutputError`,
  `AgentInputError`, `AgentCancelledError`; `classify_failure`: quota phrase, then status (401
  auth, 429 transient, other 4xx `AgentConfigError`), then whole-word auth
  markers, else transient. Claude's rejected rate-limit event and
  `billing_error` are quota too. The only retry loop is in `run`:
  transient → new session (up to `attempts`) after 30 s without a slot.
- Abort (`kill_all`, a `run_jobs` interrupt) sets `core.process.ABORT` for
  good: the retry wait (`ABORT.wait`) wakes, and no session or repair turn
  starts (`AgentCancelledError`, outcome `cancelled`, never retried). An `AgentQuotaError` latches
  its backend for the runner's lifetime: later tasks on that backend fail at
  once with `AgentQuotaError`; other backends keep running.
- Events: adapters yield `Thought`/`ToolCall`/`ToolResult`/`Message`; the
  runner emits `AgentSessionStarted` (on slot), `AgentActivity` via
  `summarize` (final message by length only) and `AgentSessionFinished`.
- Audio-unavailable protocol (runner-owned, backend-neutral, only for tasks
  with audio): `prompt.audio_check_section` offers a
  `GRILL_AUDIO_UNAVAILABLE: <reason>` line; a `Message` carrying it stops the
  turn at once → `AgentInputError` (outcome `input_error`, never repaired or
  retried; `usage` stays empty). Distinct from the agy defect for audio never
  opened, which the adapter detects and repairs.

## MCP tools (`agent_tools/`)

The runner launches, by module name (`agents` never imports `agent_tools`),
`python -m grillmaster.agent_tools --session
<session_dir>/tools.json` (argv: codex MCP servers do not inherit env). Only
tools with a sub-config in `ToolSession` exist; a bad manifest exits 2.
`get_frames(times)`: ≤20 timestamps inside the window, saved to `frames_dir`,
returned as MCP image content (with `pending_frames`: text only, paths appended
to that file). `check_srt(path)`: skeleton check of an
absolute path against `reference_srt` → `VALID` / `INVALID` + problems.
`agents/prompt.py` lists the tools (with the window); stages add guidance via
`core.prompts.frames_guidance`. A new tool = config in `core/tool_session.py`
(`ToolName` + sub-config) + handler module + registration in `server.py` +
line in `agents/prompt.py`.

## Tests

`tests/agents/` uses fakes (`FakeSpawn`, `FakeQuery`); `test_contract.py`
replays real recordings in `tests/fixtures/agents/<backend>/`. `test_live.py`
is `live`-marked (deselected by default; spends quota); after a CLI upgrade
`scripts/record_agent_fixture.py [backend...]` reruns it to re-record the
per-turn fixtures named in `fakes.LIVE_TURNS` (gemini adds `live_frames`; the
other recordings are hand-captured). `live_tool_server.py` honours
`pending_frames` like the real tool.
