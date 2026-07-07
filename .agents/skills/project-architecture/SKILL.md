---
name: project-architecture
description: >-
  Orchestration-level architecture of the Owarai GrillMaster pipeline: the
  resumable stage machine in `workflow.py`, project state and path layout in
  `project.py`, settings/`.env`/ModelSpec in `settings.py`, the Typer CLI in
  `main.py`, and the supporting services (`services/srt/`, `services/media.py`,
  `services/ytdlp/`, `services/elevenlabs/`, `services/fixed_glossary/`,
  `services/progress.py`). Read this before adding/reordering a pipeline stage,
  changing resumability or cost accounting, adding a setting, adding a source
  platform, or any task that spans more than one service module. Deep dives
  live in the sibling skills inference-layer, translate-pipeline, and
  postprocess-and-packaging.
---

# Owarai GrillMaster — Architecture

A single-user CLI that turns a Japanese variety-show video ID/URL into Traditional
Chinese subtitles (SRT + styled ASS), optionally burning them into the video.
Everything is local and resumable; there is no server, queue, or database — state
lives entirely in `projects/<id>/`.

Sibling skills own the deep detail — read the one whose files you're touching:

- **inference-layer** — `services/inference/` (backends, schema repair, frame tools)
- **translate-pipeline** — `services/translate/` (pre-pass, chunking, chunk workers, caches)
- **postprocess-and-packaging** — `services/postprocess/`, `services/finalize/`, `services/package/`

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
model for chunks that already succeeded. These caches never self-invalidate;
forcing a re-run means deleting the dot-dir.

## The pipeline (`workflow.py`)

`submit_project` creates/loads the `Project`, then `_process_project_impl` runs
the stages below in order. Each maps 1:1 to a `ProgressStage` enum value and a
`Project.is_*` boolean (see "Stage ↔ field sync" invariant).

| # | Stage (`ProgressStage`)         | What happens                                                                 | Module |
|---|----------------------------------|------------------------------------------------------------------------------|--------|
| 1 | `METADATA_FETCHED`               | `get_video_info`; for TVer/Abema also fetch cast/talents                     | `services/ytdlp` |
| 2 | `DOWNLOADED`                     | `download_video` (yt-dlp); also best-effort fetches platform CC subs (`ENABLE_OFFICIAL_SUBTITLES`). **Kicks off async cover gen here** if enabled | `services/ytdlp`, `services/postprocess/cover` |
| 3 | `VIDEO_PROCESSED`                | `MediaProcessor.combine_videos` (ffmpeg concat) → `video.mp4`; normalizes downloaded CC → `video.official.ja.srt` (section runs rebase/filter timestamps) | `services/media`, `services/ytdlp/subtitles` |
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
- **`--start` / `--to`** (either or both) process only a section. The full
  video is still downloaded (yt-dlp's `--download-sections` is ffmpeg-backed
  and slow); the video-processing stage combines to `video.full.mp4`, then
  stream-copy cuts `video.mp4` (keyframe-aligned). Ignored with a warning if
  the video is already processed.
- **Cover generation runs in a background `ThreadPoolExecutor`** started right
  after download and joined in the `finally` block — even on pipeline failure,
  because the Codex subscription cost is already incurred. Don't move the join.
  The join timeout is the hardcoded `_COVER_JOIN_TIMEOUT_SECS`. Note
  `is_cover_generated` is a `Project` boolean but **not** a `ProgressStage`:
  it is set directly in the `finally` block, not via `mark_progress`.
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
  these consistent if you add a platform. Abema has two URL kinds: episode IDs
  contain `-`/`_`; pure-alphanumeric IDs are slots (live archives) and rebuild
  as `channels/_/slots/<id>` (yt-dlp ignores the channel segment).
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

## Supporting services

- `services/srt/` — SRT primitives: `SrtBlock`, `parse_srt`, `serialize_srt`,
  timecode math. The shared subtitle data model used everywhere.
- `services/media.py` — `MediaProcessor`: ffmpeg wrappers (combine, extract
  audio, frame sampling, burn-in). FFmpeg must be on PATH. Burn-in runs with
  `-nostdin` (headless safety) and **validates output duration** afterward
  (`BURN_IN_DURATION_TOLERANCE_SECONDS`, 2 s): ffmpeg can exit 0 yet silently
  truncate, so a too-short output raises instead of shipping.
- `services/ytdlp/` — download + metadata + TVer/Abema talent scraping +
  `subtitles.py` (platform CC → `video.official.ja.srt`; single video part
  only — multi-part is skipped with a warning and the pipeline continues
  without the reference; same-part language variants prefer exact `ja`;
  consumed as ground-truth reference by the translate stages and glossary
  check — see **translate-pipeline** / **postprocess-and-packaging**). Two
  BiliBili gotchas live in `client.py`: (1) a **temporary monkey-patch** falls
  back from `/x/player/wbi/playurl` (HTTP 412 on some videos in current yt-dlp)
  to `/x/player/playurl` — remove when upstream fixes it; (2) BiliBili inputs
  are **anonymous by default** (cookies disabled) because authenticated
  cookies can lock the format list to 480p; other platforms keep cookies.
  `download.py` registers a jpeg-extension fixup PP before the thumbnail
  convertor (Abema slot thumbnails are JPEG bytes named `.png`, which breaks
  the extension-driven image2 demuxer) — keep the convertor out of the opts
  dict so the fixup stays first.
- `services/elevenlabs/` — ASR client + ASR-JSON → SRT builder. Source SRT
  formatting constants are hard-coded at the top of the builder (maintainer-tuned,
  intentionally not settings).
- `services/fixed_glossary/` — loads `fixed_glossary.json` / `.md` (canonical
  term translations) consumed by pre-pass, glossary-check, **and finalize** (it
  is a runtime input for name spacing, not just prompt content).
- `services/progress.py` — Rich progress reporter (`create_progress_reporter`,
  `NoopProgressReporter`) threaded through chunk translation and packaging.

## Settings & configuration (`settings.py`)

Pydantic-settings, loaded from `.env`. Notable patterns:

- **Per-stage backend selection**: pre-pass, chunk, and post-process each pick a
  backend + model independently (`agent_*_backend` / `agent_*_model` fields,
  i.e. `AGENT_*_BACKEND` / `AGENT_*_MODEL` in `.env`).
- **`ModelSpec`**: `*_MODEL` is written as `"model"` or `"model/effort"` (effort
  ∈ low/medium/high, default high) and parsed into `.model` + `.reasoning_effort`.
  `effort` is mapped per client (gemini thinking_level, codex
  model_reasoning_effort, claude effort). The `ModelSpecField` annotation uses
  `NoDecode` so pydantic-settings doesn't JSON-decode the shorthand string —
  any new spec-shaped field needs the same annotation.
- `AGENT_GEMINI_API_KEY` is required **only** when a stage uses `gemini-api`.
- `AGENT_GEMINI_GCP_PROJECT` is optional and applies **only** to `gemini-cli`
  (see **inference-layer** for the env handling).

## Prompts are `.md` files

Every prompt template is a `.md` under the owning module's `prompts/` dir, loaded
by a sibling `prompts.py`. **Edit the `.md` for wording; edit `prompts.py` only
for assembly logic.** See **translate-pipeline** for the audio-conditioned
substitution rules guarded by tests.

## Testing

```bash
uv run --with pytest python -m pytest          # full suite (~350 tests, seconds)
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
- New model backend → **inference-layer**.
- New translate behavior → **translate-pipeline** (keep chunk-boundary
  determinism intact).
- Refine/glossary/cover/finalize/packaging → **postprocess-and-packaging**.
- Prompt wording → the `.md`, never inline strings.
- New tunable → a `settings.py` field with a `description`; read it at the call
  site, don't thread it through constructors.
