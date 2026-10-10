---
name: agent-orchestration
description: >-
  The agent layer `src/grillmaster/agents/` (AgentTask/AgentJob, AgentRunner,
  strict schema, repair loop, errors, events, `process.py`, agy/codex/claude
  adapters) and the MCP tool server `src/grillmaster/agent_tools/`
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
  `media/ffmpeg.py`. Both register in `core.process.LIVE_PROCESSES`; the Claude
  SDK's CLI is registered by `RegisteredTransport`. Abort (TUI/CLI) and
  `atexit` call `kill_all()`, which tree-kills (`taskkill /T /F` / `killpg`).
- `agents` and `agent_tools` never import `config`/`project` (import-linter
  `domain-purity`); `ModelSpec`, `Role` and `ToolSession` live in `core/`.
- No silent degradation: missing capability/model/input file or a non-strict
  schema raises `AgentConfigError` before any slot is taken.

## Task API (`task.py`, `schema.py`)

- `AgentTask[T]`: `name` (e.g. `chunks/0001-0119`), `role`, `instructions` +
  `prompt` (sent as ONE user message on every backend), `session_dir`,
  `workdir` (cwd and writable root; `None` = temp dir; concurrent tasks may not
  share one), `output`, `images`, `audio`, `tools`, `add_dirs`, `validate`
  (raises `ValidationFailure`), `requires`, `max_repairs=3`, `attempts=1`.
- Outputs: `TextOutput`, `SchemaOutput(model)` (native structured channel),
  `FilesOutput(required, optional)` (both deleted before each attempt). A
  failed parse is a `ValidationFailure`, i.e. a repair round.
- `run(task) -> AgentResult` or a classified `AgentError`. Fan-out uses
  `run_jobs(jobs) -> list[JobFailure]` with `AgentJob(name, prepare, accept)`:
  `prepare` builds the task on the worker before it takes a slot, `accept`
  persists the result right after its session. An exception fails only its
  job; failures return in input order and the caller raises after the batch.
  Ctrl-C cancels unstarted jobs and lets running ones finish and accept.
- `strict_json_schema`: refs inlined, all properties required,
  `additionalProperties: false`, object root; free-key dicts and recursive
  models are rejected (use `T | None`, lists of objects). Pydantic is the judge.

## Roles and capabilities

`grill.toml [agents.roles]` → `AgentRoles.specs()` (`chat` falls back to
`utility`) → `dict[Role, ModelSpec]`, written `backend/model[/effort]`
(default `high`). `[agents] max_concurrent` sizes the global semaphore;
`timeout_minutes` bounds each turn (per-process watchdog), not the session.

All adapters declare IMAGE_INPUT, NATIVE_SCHEMA, RESUME, MCP, MCP_IMAGE_RESULT,
WEB_SEARCH; only agy has AUDIO_INPUT, only codex IMAGE_GENERATION. Required =
`task.requires` (what inputs cannot imply: IMAGE_GENERATION for cover,
WEB_SEARCH for prepass/titles/date research) + derived: schema→NATIVE_SCHEMA,
`max_repairs > 0`→RESUME, images→IMAGE_INPUT, audio→AUDIO_INPUT, tools→MCP,
frames tool→MCP_IMAGE_RESULT.
Stages check `runner.capabilities(role)` (`accepts_audio`) before adding audio.

## Adapters (`adapters/`, loaded lazily by `AdapterRegistry`)

`base.py`: `media_delivery` (`ATTACHED`, or `VIEW_FILE`: the runner lists
absolute paths to open), `preflight`, `start`/`resume(session_id, TurnRequest)`
→ `SessionHandle` → `FinalOutput(session_id, text, structured, usage,
defects)`. A resume repeats every start setting with a new `message`.

- **agy**: `--input-format stream-json --output-format stream-json -p=`, one
  text-only stdin line, stdin closed on `result`. Model id
  `<model>-<low|medium|high>` checked once against `agy models`. Audio not
  opened by a finished `view_file` step → `TurnDefect` resending it.
  `--json-schema` output trusted only if `finish` ran this turn. MCP from
  `<workdir>/.agents/mcp_config.json` (deleted when no tools). Resume
  `--conversation`. `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_API_KEY`
  are stripped so agy stays on the subscription login.
- **codex**: `codex exec --json … -`, `--ignore-user-config`, sandbox bypassed,
  `--image` on the start turn only, `--output-schema` (last `agent_message`),
  `-c mcp_servers.grill.*`, `-c tools.web_search=true` only when required.
  `exec resume <thread>` has no `--cd`: the process cwd is the workdir.
- **claude**: SDK `query()` under `asyncio.run` on a per-turn thread (the only
  module allowed asyncio); `setting_sources=[]` + `strict_mcp_config=True`,
  base64 image blocks, `output_format`, `resume=`. Stream-captured rate-limit
  and error details win over the SDK's opaque exception.

## Runner loop (`runner.py`)

- Each attempt gets a fresh session dir (`session/`, `session.2/`, …):
  `prompt.md`, `tools.json`, `schema.json`, `raw.jsonl` (verbatim stream),
  `result.json` (`SessionRecord`); agy adds `agy.log`.
- One global slot is held from the first turn through every repair. Adapter
  defects first, then parse + `validate`; a failure resumes the same session
  with only `prompt.repair_message(...)`. Exhausted repairs →
  `AgentOutputError`. Nested `run`/`run_jobs` inside a slot is refused.
- Errors: `AgentConfigError`, `AgentQuotaError` (429), `AgentAuthError` (401 +
  login hint), `AgentTransientError` (timeout, crash, no result),
  `AgentOutputError`; `classify_failure` maps status, then markers, else
  transient. The only retry loop is in `run`: transient → new session (up to
  `attempts`) after 30 s without a slot.
- Events: adapters yield `Thought`/`ToolCall`/`ToolResult`/`Message`; the
  runner emits `AgentSessionStarted` (on slot), `AgentActivity` via
  `summarize` (final message by length only) and `AgentSessionFinished`.

## MCP tools (`agent_tools/`)

The runner launches `python -m grillmaster.agent_tools --session
<session_dir>/tools.json` (argv: codex MCP servers do not inherit env). Only
tools with a sub-config in `ToolSession` exist; a bad manifest exits 2.
`get_frames(times)`: ≤20 timestamps inside the window, saved to `frames_dir`,
returned as MCP image content. `check_srt(path)`: skeleton check of an
absolute path against `reference_srt` → `VALID` / `INVALID` + problems.
`agents/prompt.py` lists the tools (with the window); stages add guidance via
`core.prompts.frames_guidance`. A new tool = config in `core/tool_session.py`
+ handler in `server.py` + line in `agents/prompt.py`.

## Tests

`tests/agents/` uses fakes (`FakeSpawn`, `FakeQuery`); `test_contract.py`
replays real recordings in `tests/fixtures/agents/<backend>/`. `test_live.py`
is `live`-marked (deselected by default; spends quota); after a CLI upgrade
`scripts/record_agent_fixture.py [backend...]` reruns it to re-record fixtures.
