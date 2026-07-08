---
name: inference-layer
description: >-
  The unified model-inference layer under `services/inference/`. Read this
  before touching `run_inference`, any backend file (`gemini_api.py`,
  `gemini_cli.py`, `gemini_agy.py`, `codex.py`, `claude_sdk.py`), capability
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

Five backends (`Backend` StrEnum in `base.py`):

| Backend       | Auth            | Audio? | Schema handling | Cost |
|---------------|-----------------|--------|-----------------|------|
| `gemini-api`  | API key         | ✅     | native `response_json_schema` | metered (the only paid backend) |
| `gemini-cli`  | subscription    | ✅     | prompt-appended + repair loop | free |
| `gemini-agy`  | subscription    | ❌     | prompt-appended + repair loop | free |
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
  "everything except gemini-api".
- **`web_search=True` enables the backend's built-in web tools** (agent
  backends only; gemini-api raises). Explicit enablement is required for codex
  (`-c tools.web_search=true` — off by default in `codex exec`; `--yolo` does
  not add the tool) and gemini-cli (policy allow rules for
  `google_web_search`/`web_fetch`); claude and gemini-agy already expose them
  under their permission bypass, so their runners treat the flag as a no-op.
- **Schema enforcement is shared, not per-backend** (`schema_enforce.py`): for
  the prompt-based backends, `run_inference` appends the schema instruction once
  and runs the validate-and-repair loop centrally. Each backend's only job is
  `prompt → text`. The retry cap is the hardcoded `MAX_SCHEMA_RETRIES` constant
  there (not a setting).
- **Reasoning effort is repo-normalized**: callers pass
  `reasoning_effort` as low/medium/high/extra. Backend wrappers map that to
  their real values: Codex and Claude use `xhigh` for repo-level `extra`;
  Gemini API and gemini-agy have no extra-high value and clamp `extra` to
  `HIGH` / `High`; gemini-cli exposes no separate effort flag, so only the
  model id is passed to that CLI.

Backend runtime gotchas:

- `gemini_cli.py` generates a **temporary TOML policy file at runtime** that
  whitelists only the frame-tool wrapper command prefixes, and uses
  include-dirs instead of `--yolo` (yolo's sandboxing breaks project-local
  frame reads). `AGENT_GEMINI_GCP_PROJECT` is injected as a temporary
  `GOOGLE_CLOUD_PROJECT`; API-key env vars are scrubbed from the subprocess so
  it never silently switches to API-key billing.
- `gemini_agy.py` (Antigravity CLI) **must run under a pty** (`pywinpty` on
  Windows, stdlib `pty` on POSIX) — `agy -p` drops stdout on a non-TTY — and
  stages the prompt (and images) into a temporary workspace file referenced by
  `@<file>` tokens; the workspace is cleaned up after the call.

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
