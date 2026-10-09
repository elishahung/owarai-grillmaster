---
name: inference-layer
description: >-
  The unified model-inference layer under `services/inference/`. Read this
  before touching `run_inference`, any backend file (`gemini_api.py`,
  `agy.py`, `codex.py`, `claude_sdk.py`), capability
  gating in `base.py`, the schema validate-and-repair loop in
  `schema_enforce.py`, or the agent frame tools under
  `services/inference/tools/` (`get_frames*.py`). Also read it when adding or
  renaming a `Backend`, or changing how audio/images/schema/prompts reach a
  model.
---

# Unified inference layer (`services/inference/`)

**One entry point** for every model call in the repo:

```python
run_inference(*, backend, prompt, system_prompt=None, cwd=None,
              images=None, audio=None, schema=None, model=None,
              reasoning_effort="high", output_last_message_path=None,
              timeout=None, web_search=False) -> InferenceResult
```

Four backends (`Backend` StrEnum in `base.py`):

| Backend       | Auth            | Audio? | Schema handling | Cost |
|---------------|-----------------|--------|-----------------|------|
| `gemini-api`  | API key         | ✅     | native `response_json_schema` | metered (the only paid backend) |
| `agy`  | subscription    | ✅     | prompt-appended + repair loop | free |
| `codex`       | subscription    | ❌     | prompt-appended + repair loop | free |
| `claude`      | subscription    | ❌     | prompt-appended + repair loop | free |

Design rules baked into this layer — preserve them:

- **One call, parameterized — not modes.** `schema=None` → return the model's raw
  final text (agentic file-writing callers pass `cwd` and inspect files after).
  `schema=<Model>` → JSON guaranteed-parseable for that model.
- **Capability gating lives in `base.py`** (`backend_supports_audio`,
  `is_agent_backend`, `is_gemini_backend`). Passing audio to a non-audio backend
  raises `UnsupportedMediaError`. Callers must gate audio on the *backend's*
  capability, not just on whether an audio asset exists. `is_agent_backend` is
  "everything except gemini-api". Subscription quota / rate-limit errors
  subclass `InferenceQuotaError`; stages catch that, never a backend class.
- **`web_search=True` enables the backend's built-in web tools** (agent
  backends only; gemini-api raises). Explicit enablement is required for codex
  (`-c tools.web_search=true` — off by default in `codex exec`; `--yolo` does
  not add the tool); claude and agy already expose them
  under their permission bypass, so their runners treat the flag as a no-op.
  Every agent-instruction stage (pre-pass, chunk, refine, glossary check,
  date research) passes `web_search=is_agent_backend(backend)` (date research
  hardcodes `True` since its backends are always agentic).
- **Output enforcement is shared, not per-backend** (`schema_enforce.py`):
  every backend runs through the same validate-and-repair loop; the branches
  differ only in how the schema reaches the model (native for gemini-api,
  prompt-appended for the rest). Each backend's only job is `prompt → text`;
  `invoke_once` returns an `InferenceResult` and the loop sums cost/requests
  across attempts. The retry cap is the hardcoded `MAX_SCHEMA_RETRIES` constant
  there (not a setting).
- **`validate=` enforces invariants the schema cannot express** (requires
  `schema=`): a callback over the parsed model that raises `ValueError` to
  reject output; the message is fed back verbatim as the repair instruction.
  Use it whenever schema-valid-but-wrong is a real failure mode (e.g. a list
  that must hold one entry per input range). It applies to every backend — do
  not reintroduce a guard that only prompt-based backends honor.
- **The per-invocation timeout is one setting for every backend**:
  `base.default_timeout_secs()` reads `AGENT_TIMEOUT_MINUTES` (default 40 min)
  per call — never cache it in a module constant. A `timeout=` argument
  overrides it.
- **Reasoning effort is repo-normalized**: callers pass
  `reasoning_effort` as low/medium/high/extra/max/ultra. Backend wrappers map
  that to their real values: Codex and Claude use `xhigh` for repo-level
  `extra` and `max` for `max`; Codex passes `ultra` through (only some models
  accept it) while Claude clamps `ultra` to `max`; Gemini API and agy
  have no extra-high value and clamp `extra`/`max`/`ultra` to
  `HIGH` / `High` (agy bakes effort into its `--model` display name).

Backend runtime gotchas:

- CLI-shim subprocesses (`codex.py`) must run via
  `base.run_cli`, which **tree-kills on timeout** (`taskkill /T /F` on
  Windows, killpg on POSIX) — plain `subprocess.run(timeout=)` kills only the
  cmd.exe shim and then blocks forever draining pipes the hung node
  grandchild still holds; do not revert to it.
- `claude_sdk.py` preserves structured error details from the stream before the
  SDK replaces them with its opaque trailing exception: HTTP 401 includes the
  subscription re-login command, 429 remains `ClaudeSDKRateLimitError`, and
  other API failures retain their HTTP status and provider message.
- `agy.py` (Antigravity CLI) **must run under a pty** (`pywinpty` on
  Windows, stdlib `pty` on POSIX) — `agy -p` drops stdout on a non-TTY — and
  stages the prompt (and images) into a temporary workspace file referenced by
  `@<file>` tokens; the workspace is cleaned up after the call. API-key env
  vars are scrubbed so it stays on the subscription login.
  **Audio does not attach via `@`** (agy >= 1.1.18): the bootstrap tells the
  agent to open each audio file (read in place; its directory is
  `--add-dir`'d, not copied) with its `view_file` tool, which yields
  a native `audio/*` media part. After the run, the conversation id from
  `--log-file` locates `~/.gemini/antigravity-cli/brain/<id>/.system_generated/
  logs/transcript_full.jsonl`; fewer audio media parts than audio files raises
  `AgyError`, and so does a missing conversation id or transcript. Model ids map to agy display
  names via `_AGY_MODEL_BASES` — refresh it from `agy models` when Google
  rotates models.

## Agent frame tools (`services/inference/tools/`)

`get_frames.py` is a CLI agent backends run mid-session to extract up to 20
frames at specific `--times` for a moment they need to see. Stage-specific
wrapper scripts (`get_frames_for_pre_pass.py`, `..._for_chunk.py`,
`..._for_refine.py`, `..._for_glossary_check.py`) pre-fill the stage and output
directory; the agent should only pass `--project-dir` and replace the `--times`
value. Extra frames land next to the stage artifacts:
`.pre_pass/media/extra_frames/`, `.chunks/media/extra_frames/`,
`.refine/extra_frames/`, `.glossary_check/extra_frames/` — treat files there as
the audit signal for whether the tool was actually used.

`build_*_frame_tool_instruction` renders frame usage;
`build_pre_pass_agent_instruction` adds bounded agent-only web-search guidance.
These blocks are appended to stage prompts **only when
`is_agent_backend(backend)`** — gemini-api can't run local tools and never sees
them.

## Where to make a change

- New backend → add to `Backend` + capability sets in `base.py`, a `run_*`
  backend file, dispatch in `run_inference` (`__init__.py`).
- Test patterns for mocking backends live in `tests/test_inference.py`.
