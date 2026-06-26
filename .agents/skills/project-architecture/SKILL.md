---
name: project-architecture
description: >-
  Detailed architecture reference for the Owarai GrillMaster pipeline (video
  download → ASR → two-stage translation → post-process → finalize → package).
  Consult this skill BEFORE making non-trivial changes anywhere under
  `services/`, `workflow.py`, `project.py`, or `settings.py` — whenever you
  touch the pipeline stages, the unified inference layer, the translate package
  (pre-pass / chunk / structural-fix), post-processing, finalize, packaging, or
  add/rename a backend, stage, or setting. Read it to learn where a concern
  lives and which invariants must hold, so a change lands in the right module
  instead of being bolted on. Also consult it when onboarding to the codebase
  or when a task spans more than one service module.
---

# Owarai GrillMaster — Architecture

A single-user CLI that turns a Japanese variety-show video ID/URL into Traditional
Chinese subtitles (SRT + styled ASS), optionally burning them into the video.
Everything is local and resumable; there is no server, queue, or database — state
lives entirely in `projects/<id>/`.

## Mental model

The whole program is a **linear, idempotent, resumable stage machine**. One
`Project` (a Pydantic model persisted as `projects/<id>/project.json`) carries a
boolean per stage. `workflow.py` runs the stages in order; each stage checks its
boolean, skips if already done, and on success calls `project.mark_progress(...)`
which flips the boolean and re-saves the JSON. Re-running the same ID resumes
exactly where it left off. This is the central invariant — **every new stage must
preserve it**.

The expensive model-driven stages additionally cache their *intermediate* media
and responses under dot-dirs (`.asr/`, `.pre_pass/`, `.chunks/`, …) so that a
resume after a crash does not re-extract audio, re-sample frames, or re-call the
model for chunks that already succeeded.

## The pipeline (`workflow.py`)

`submit_project` creates/loads the `Project`, then `_process_project_impl` runs
the stages below in order. Each maps 1:1 to a `ProgressStage` enum value and a
`Project.is_*` boolean (see "Stage ↔ field sync" invariant).

| # | Stage (`ProgressStage`)         | What happens                                                                 | Module |
|---|----------------------------------|------------------------------------------------------------------------------|--------|
| 1 | `METADATA_FETCHED`               | `get_video_info`; for TVer/Abema also fetch cast/talents                     | `services/ytdlp` |
| 2 | `DOWNLOADED`                     | `download_video` (yt-dlp). **Kicks off async cover gen here** if enabled     | `services/ytdlp`, `services/postprocess/cover` |
| 3 | `VIDEO_PROCESSED`                | `MediaProcessor.combine_videos` (ffmpeg concat) → `video.mp4`                | `services/media` |
| 4 | `AUDIO_PROCESSED`                | `MediaProcessor.extract_audio` → `.asr/audio.ogg` (mono 16 kHz libopus)      | `services/media` |
| 5 | `ASR_COMPLETED`                  | ElevenLabs Scribe → `.asr/asr.json`; adds cost                               | `services/elevenlabs` |
| 6 | `SRT_COMPLETED`                  | `convert_file` ASR JSON → `video.ja.srt`                                     | `services/elevenlabs/srt_builder` |
| 7 | `PREPASS_COMPLETED`              | One whole-film analysis call → `.pre_pass/pre_pass.json`                     | `services/translate` (pre_pass) |
| 8 | `CHUNK_TRANSLATED`               | Concurrent per-chunk translation → `video.cht.srt`                          | `services/translate` (chunk) |
| 9 | `SRT_REFINED` (optional)         | Agent polishes TC subtitles → `video.cht.refined.srt`                        | `services/postprocess/refine` |
| 10| `GLOSSARY_CHECKED` (optional)    | Agent checks full-text terminology/facts, may correct `pre_pass.json` → `video.cht.glossary_checked.srt`| `services/postprocess/glossary_check` |
| 11| `FINALIZED`                      | Punctuation cleanup → styled `video.cht.ass` + `video.cht.finalized.srt`     | `services/finalize` |

After `FINALIZED` (and only if no `--break-after`): join the async cover future,
optionally `archive()` the project dir, then `package_project` (burn-in + cover
copy / remix). Archive and package are **post-loop**, not stages.

Key control-flow details that are easy to break:

- **`--break-after <stage>`** stops cleanly *after* the named stage (works on a
  fresh or resumed project). When set, cover generation is skipped entirely.
- **Cover generation runs in a background `ThreadPoolExecutor`** started right
  after download and joined in the `finally` block — even on pipeline failure,
  because the Codex subscription cost is already incurred. Don't move the join.
- **Optional stages are gated twice**: by a `settings.enable_*` toggle OR a
  per-run `--refine/--glossary-check/--cover` flag (the flag force-enables).
- **Cost accounting**: metered stages call `project.add_cost(service, amount)`,
  which accumulates into `project.json`. `TranslationError` carries a partial
  cost summary so a mid-run failure still records what was spent.
- **Finalize input precedence**: glossary-checked SRT → refined SRT → translated
  SRT (first that exists wins).

## Project state (`project.py`)

`Project` is the single source of truth. It owns:

- **Identity**: `parse_source_str` extracts the canonical ID from an ID or URL
  across Bilibili (`BV…`), YouTube (stored as `v=…`), TVer (`ep…`/`sh…`), Abema
  (fallback). `source` and `source_url` are derived from the ID's shape — keep
  these consistent if you add a platform.
- **All path properties** (`video_path`, `srt_path`, `pre_pass_path`,
  `chunks_cache_dir`, …). **Never hard-code a project file path elsewhere** — add
  or read a property here so the layout stays in one place.
- **Cross-episode seeding**: `parent_project_path` → `parent_pre_pass_context()`
  reads a parent project's `pre_pass.json` to keep names/terms consistent across
  episodes. It raises early (before any model cost) if the parent is missing.
- **Stage ↔ field sync invariant**: `check_enum_field_sync()` runs at import and
  asserts every `ProgressStage` value names a real `Project.is_*` field. If you
  add a stage you MUST add both the enum value and the boolean field, or import
  fails fast.

## The unified inference layer (`services/inference/`)

This is the most important abstraction and the subject of the recent refactor
(it was previously split as `agent_exec` + `gemini`). **One entry point**:

```python
run_inference(*, backend, prompt, system_prompt=None, cwd=None,
              images=None, audio=None, schema=None, model=None,
              reasoning_effort="high", ...) -> InferenceResult
```

Four backends (`Backend` StrEnum in `base.py`):

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
  capability, not just on whether an audio asset exists.
- **Schema enforcement is shared, not per-backend** (`schema_enforce.py`): for the
  three prompt-based backends, `run_inference` appends the schema instruction
  once and runs the validate-and-repair loop centrally. Each backend's only job
  is `prompt → text`. The retry cap is the hardcoded `MAX_SCHEMA_RETRIES`
  constant there (not a setting).

Backend files: `gemini_api.py`, `gemini_cli.py`, `gemini_agy.py` (Antigravity
CLI; must run under a pty and stage the prompt to a file — `agy -p` drops stdout
on a non-TTY and takes the prompt as an argv arg), `codex.py`, `claude_sdk.py`;
shared: `base.py` (contract/errors), `result.py` (`InferenceResult`),
`schema_enforce.py`.

**Agent-facing tools (`services/inference/tools/`)** — `get_frames.py` is a CLI
agent backends run mid-session to extract up to 20 frames at specific `--times`
for a moment they need to see. Stage-specific wrapper scripts pre-fill the stage
and output directory; the agent should only pass `--project-dir` and replace the
`--times` value. Extra frames are written next to the stage artifacts:
`.pre_pass/media/extra_frames/`, `.chunks/media/extra_frames/`,
`.refine/extra_frames/`, and `.glossary_check/extra_frames/`. Treat files in
these directories as the audit signal for whether the tool was actually used.
Gemini CLI allows only these wrappers via policy/include-dirs instead of
`--yolo`, because yolo's sandboxing breaks project-local frame reads.
`build_*_frame_tool_instruction` appends usage to the pre-pass/chunk/refine/
glossary-check prompts **only when `is_agent_backend(backend)`** — keeping
gemini-api's prompt byte-stable.

## The translate package (`services/translate/`)

Two-stage translation orchestrated by `facade.py` (`class Translate`). Both
stages call `Translate._prepare`, which parses the SRT and splits it into
char-balanced chunks **deterministically** so pre-pass and chunk stages always
agree on boundaries — this determinism is load-bearing.

```
services/translate/
├── facade.py        # Translate: run_pre_pass + translate_chunks (asyncio)
├── chunker.py       # split_into_chunks (char-balanced)
├── assets.py        # frame sampling + per-chunk audio slicing (ffmpeg), cached
├── request.py       # TranslationRequest (paths + context bundle)
├── errors.py        # TranslationError / ChunkTranslationError + cost summaries
├── pre_pass/
│   ├── pre_pass.py  # whole-film analysis → pre_pass.json
│   ├── schema.py    # PrePassResult / characters / catchphrases / SegmentSummary
│   └── prompts/     # *.md prompt templates
└── chunk/
    ├── chunk_worker.py    # translate_chunk: cache → infer → validate → fix
    ├── prompts.py + prompts/   # system instruction, audio-conditioned
    ├── validation.py / validate_chunk.py  # structural validation
    ├── structural_fix.py  # agent self-validating repair (fix_chunk_structure)
    └── normalizer.py      # merge + reindex across chunks
```

### Pre-pass (stage 7)

One call over the **whole** film (full SRT + program info + full audio for gemini
backends + 20-40 SRT-start-aligned representative frames + optional fixed-glossary + optional
parent context). Produces a `PrePassResult` briefing: character roster, proper
nouns / ASR-correction dict, catchphrase fixed translations, overall tone, and a
**per-segment summary keyed by the exact chunk index ranges**. Persisted to
`.pre_pass/pre_pass.json` — this file is the explicit hand-off to the chunk stage.

### Chunk translation (stage 8) — `chunk_worker.translate_chunk`

Per chunk, concurrently (semaphore-bounded: `chunk_api_concurrency` for the
network `gemini-api` backend, lower `chunk_agent_concurrency` for the agent
backends — gemini-cli/codex/claude — since each spawns a heavy local process;
`is_agent_backend` is "everything except gemini-api"). The worker is a careful
cache-and-repair ladder:

1. **Raw cache** (`chunk_XXXX-YYYY_<digest>.raw.srt`) keyed on backend+model+
   message — a hit skips the model call entirely.
2. Build the user message: pre-pass briefing (global + this segment's summary) +
   the chunk's frame timestamps + the SRT slice. Call `run_inference`
   (`schema=None`, free-form SRT out) with retries + exponential backoff.
3. **Strict structural validation** (`validate_chunk_structure`) checks that
   every source timecode appears exactly once, there are no unexpected or
   duplicate timecodes, the block count matches, and every output block has
   non-empty translated text.
4. On validation failure, the worker invokes the **agent fix layer**
   (`fix_chunk_structure`): hand raw output + the source skeleton + the error to
   an agent backend that self-validates until it passes. The agent may translate
   a genuinely missing block from `source.srt`, but it must preserve the source
   skeleton and cannot leave blank placeholder blocks. The repaired result is
   cached as `…_<>.fixed.srt`.

`facade._translate_chunks_async` gathers all chunks (collecting partial costs and
per-chunk failures into a `TranslationError` summary on failure), then
`normalizer.normalize_translated_blocks` merges and **reindexes to contiguous
1..N** before writing `video.cht.srt`.

## Post-processing (`services/postprocess/`)

Optional agent passes, each a thin orchestrator over `run_inference` where the
agent reads/writes files in the project dir and we validate afterward
(`_srt_guard.py` guards line-count/structure):

- `refine.py` — polish TC subtitles (`AGENT_POSTPROCESS_BACKEND`).
- `glossary_check.py` — full-text terminology/factual consistency check after
  refine. It always runs when enabled unless `video.cht.glossary_checked.srt`
  already exists, treats Latin/kana blocks only as priority hints, may use web
  search or on-demand frames, and may correct `.pre_pass/pre_pass.json` after
  preserving the original as `.pre_pass/pre_pass.raw.json`. The updated
  pre-pass must still validate against `PrePassResult`.
- `cover.py` — stylize the poster. **Always Codex** (image generation), regardless
  of the post-process backend setting. Runs async (see pipeline notes).

`__init__.py` uses lazy `__getattr__` imports so importing the package doesn't
drag in every backend.

## Other services

- `services/srt/` — SRT primitives: `SrtBlock`, `parse_srt`, `serialize_srt`,
  timecode math. The shared subtitle data model used everywhere.
- `services/finalize/` — SRT → styled ASS + cleaned SRT. Applies Netflix-TC
  punctuation rules (strip terminal commas/periods, collapse ellipses, convert
  mid-line `。`→`，`, etc.). The ASS style header lives here.
- `services/media.py` — `MediaProcessor`: ffmpeg wrappers (combine, extract
  audio, frame sampling, burn-in). FFmpeg must be on PATH.
- `services/ytdlp/` — download + metadata + TVer/Abema talent scraping.
- `services/elevenlabs/` — ASR client + ASR-JSON → SRT builder. Source SRT
  formatting constants are hard-coded at the top of the builder (maintainer-tuned,
  intentionally not settings).
- `services/fixed_glossary/` — loads `fixed_glossary.json` / `.md` (canonical
  term translations) consumed by pre-pass, glossary-check, and finalize.
- `services/package/` — deliverable assembly: burn ASS into video (`core.py`),
  copy cover and analysis artifacts (`pre_pass.json`, optional
  `refine.md`/`glossary_check.md`), plus a `noise`/`remix` packaging path
  (`noise.py`, `remix.py`).
- `services/progress.py` — Rich progress reporter (`create_progress_reporter`,
  `NoopProgressReporter`) threaded through chunk translation and packaging.

## Settings & configuration (`settings.py`)

Pydantic-settings, loaded from `.env`. Notable patterns:

- **Per-stage backend selection**: pre-pass, chunk, and post-process each pick a
  backend + model independently (`AGENT_*_BACKEND`, `AGENT_*_MODEL`).
- **`ModelSpec`**: `*_MODEL` is written as `"model"` or `"model/effort"` (effort
  ∈ low/medium/high, default high) and parsed into `.model` + `.reasoning_effort`.
  `effort` is mapped per client (gemini thinking_level, codex
  model_reasoning_effort, claude effort). The `ModelSpecField` annotation uses
  `NoDecode` so pydantic-settings doesn't JSON-decode the shorthand string.
- `AGENT_GEMINI_API_KEY` is required **only** when a stage uses `gemini-api`.

## Prompts are `.md` files

Every prompt template is a `.md` under the owning module's `prompts/` dir, loaded
by a sibling `prompts.py`. **Edit the `.md` for wording; edit `prompts.py` only
for assembly logic.** The chunk instruction has an audio-conditioned variant:
`build_chunk_instruction(has_audio=...)` applies verbatim find/replace pairs so
the `has_audio=True` text stays **byte-identical** to the historical constant
(gemini prompt-cache stability is asserted by a unit test in
`tests/test_translate_prompts.py`). When editing `chunk.md`, keep the strings the
no-audio substitution searches for intact, or that test will fail.

## Testing

```bash
uv run --with pytest python -m pytest          # full suite (~281 tests, seconds)
uv run --with pytest python -m pytest tests/test_inference.py        # one file
uv run --with pytest python -m pytest -k chunk_validation            # by keyword
```

`python -m pytest` (not bare `pytest`) is required so the repo root is on
`sys.path` and `services`/`project`/`workflow` import. pytest is **not** a project
dependency — it's pulled in ephemerally via `uv run --with pytest`. Tests are
fast and fully offline (model/network calls are mocked); there is no CI.

## Where to make a change (cheat sheet)

- New pipeline stage → add `ProgressStage` value **and** `Project.is_*` field
  (import-time sync check), wire it into `_process_project_impl` with the
  skip-if-done + `mark_progress` pattern, add path properties on `Project`.
- New model backend → add to `Backend` enum + capability sets in
  `inference/base.py`, a `run_*` backend file, dispatch in `run_inference`.
- New translate behavior → it almost always belongs in `pre_pass/` or `chunk/`,
  not `facade.py`. Keep chunk-boundary determinism intact.
- Prompt wording → the `.md`, never inline strings.
- New tunable → a `settings.py` field with a `description`; read it at the call
  site, don't thread it through constructors.
