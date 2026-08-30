---
name: project-architecture
description: >-
  Orchestration-level architecture of the Owarai GrillMaster pipeline: the
  resumable stage machine in `workflow/`, project state and path layout in
  `project.py`, settings/`.env`/ModelSpec in `settings.py`, the Typer CLI in
  `main.py`, and the supporting services (`services/srt/`, `services/media.py`,
  `services/ytdlp/`, `services/elevenlabs/`, `services/fixed_glossary/`,
  `services/progress.py`, `services/paths.py`, `services/tui/`). Read this before adding/reordering a pipeline stage,
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
boolean per stage. The `workflow/` package runs the stages in order; each stage checks its
boolean, skips if already done, and on success calls `project.mark_progress(...)`
which flips the boolean and re-saves the JSON. Re-running the same ID resumes
exactly where it left off. This is the central invariant — **every new stage must
preserve it**.

The expensive model-driven stages additionally cache their *intermediate* media
and responses under dot-dirs (`.asr/`, `.pre_pass/`, `.chunks/`, …) so that a
resume after a crash does not re-extract audio, re-sample frames, or re-call the
model for chunks that already succeeded. These caches never self-invalidate;
forcing a re-run means deleting the dot-dir.

## The pipeline (`workflow/`)

`workflow/__init__.py` is the public facade (`submit_project`, `process_project`,
`ProgressStage`). `workflow/api.py` creates/loads the `Project`, then
`_process_project_impl` runs the stages below in order through
`WorkflowRunner`. Each maps 1:1 to a `ProgressStage` enum value and a
`Project.is_*` boolean (see "Stage ↔ field sync" invariant). Stage bodies live
under `workflow/stages/` by subsystem; post-finalize archive/package is in
`workflow/delivery.py`; cover/date background futures are managed by
`workflow/side_tasks.py`.

| # | Stage (`ProgressStage`)         | What happens                                                                 | Module |
|---|----------------------------------|------------------------------------------------------------------------------|--------|
| 1 | `METADATA_FETCHED`               | `get_video_info`; for TVer/Abema also fetch cast/talents; `resolve_broadcast_date` persists `Project.broadcast_date` (best-effort). **Kicks off async broadcast-date research after this stage** if enabled and the date is still None | `services/ytdlp`, `services/postprocess/date_research` |
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

After `FINALIZED` (and only if no `--break-after`): join the async cover and
date-research futures,
optionally `archive()` the project dir, then `package_project` (burn-in + cover
copy / remix). Archive and package are **post-loop**, not stages. Package output
is flat: `Project.package_dir(PACKAGE_PATH)` = `{deliverable_name}` —
`YYMMDD_{id}_{name}` when `broadcast_date` is known (announced on-air/publish
date, platform-local timezone), plain `{id}_{name}` otherwise. Archive nests by
date instead: `Project.archive_dir(ARCHIVED_PATH)` =
`YY/MM/YYMMDD_{id}_{name}`, `etc/{id}_{name}` when undated; `archive()` only
rmtree's the leaf dir, never the shared `YY/MM`/`etc` parents. Both destinations
go through `services/paths.fit_dir_name`, which trims `{name}` (never the
`deliverable_stem` identity prefix) so the deepest nested artifact stays inside
the platform path limit — Windows MAX_PATH 260, since ffmpeg/yt-dlp are not
long-path aware. So the on-disk leaf can be shorter than `deliverable_name`;
always resolve destinations through those two methods.

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
  The join timeout is the hardcoded `_COVER_JOIN_TIMEOUT_SECS` in
  `workflow/side_tasks.py`. Note
  `is_cover_generated` is a `Project` boolean but **not** a `ProgressStage`:
  it is set directly in the `finally` block, not via `mark_progress`.
- **Broadcast-date research** (`ENABLE_BROADCAST_DATE_AGENT_FALLBACK` /
  `--date-research`, default off) follows the same side-task pattern: started
  after `METADATA_FETCHED` when `broadcast_date` is None, joined in the same
  `finally` before archive/package (the deliverable name needs the date).
  The worker only writes `.artifacts/date_research.json` (fixed-filename
  cache; corrupt files count as a miss; delete to re-run); the main thread
  applies the verdict and sets `is_broadcast_date_researched` (also not a
  `ProgressStage`) — set even on an "unknown" verdict so a resume doesn't
  re-spend tokens. A completed artifact left by a previous run is applied at
  kick-off even when the fallback is disabled (the result is already paid
  for); only the agent dispatch is gated on the flag.
- **Optional stages are gated twice**: by a `settings.enable_*` toggle OR a
  per-run `--refine/--glossary-check/--cover/--date-research` flag (the flag
  force-enables).
- **Cost accounting**: metered stages call `project.add_cost(service, amount)`,
  which accumulates into `project.json`. `TranslationError` carries a partial
  cost summary so a mid-run failure still records what was spent.
- **Stage timing logs**: successful main stages go through `WorkflowRunner`,
  which logs `Stage complete: ... (<elapsed>)` after the action and
  `mark_progress` finish. Cover/date side tasks use the same elapsed suffix,
  measured from async dispatch to join.
- **Finalize input precedence**: glossary-checked SRT → refined SRT → translated
  SRT (first that exists wins).
- **Future architecture direction**: the package still keeps the pipeline as an
  explicit ordered sequence in `workflow/api.py`. If stage count or branching
  grows, the next step is a declarative stage registry built on `StageSpec` and
  `WorkflowRunner`, but do not introduce that extra indirection until it removes
  real branching or repetition.

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
  (`BURN_IN_DURATION_TOLERANCE_SECONDS`, 2 s) against
  `package_output_duration(source)` (`source / PACKAGE_TEMPO`), not the raw
  source length: ffmpeg can exit 0 yet silently truncate or skip the tempo,
  so an output more than 2 s off the expected length raises instead of
  shipping.
- `services/ytdlp/` — download + metadata + TVer/Abema talent scraping +
  `broadcast_date.py` (resolves the announced on-air/publish date: YouTube/
  BiliBili from yt-dlp timestamps, TVer from `broadcastDateLabel` month/day +
  year inferred from the availability start, Abema from program `broadcastAt`
  or slot `startAt`; all best-effort → None) +
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
  dict so the fixup stays first. Downloads auto-retry once after a short
  delay (Abema regularly fails the first attempt); options and the progress
  hook are rebuilt per attempt.
- `services/paths.py` — platform path-length mechanics (`measure`,
  `fit_dir_name`, `MAX_PATH_UNITS`/`MAX_COMPONENT_UNITS`). Owns *how* to fit a
  name; `project.py` owns the per-destination reserves
  (`PROJECT_INNER_PATH_RESERVE`, `PACKAGE_INNER_PATH_RESERVE`) — bump the
  project reserve if a deeper artifact path is ever added.
- `services/elevenlabs/` — ASR client + ASR-JSON → SRT builder. Source SRT
  formatting constants are hard-coded at the top of the builder (maintainer-tuned,
  intentionally not settings).
- `services/fixed_glossary/` — loads `fixed_glossary.json` / `.md` (canonical
  term translations) consumed by pre-pass, glossary-check, **and finalize** (it
  is a runtime input for name spacing, not just prompt content).
- `services/progress.py` — the reporter contract. `NoopProgressReporter` is the
  base protocol: legacy bars (`start_stage/advance/finish`), chunk callbacks,
  and structured lifecycle events (`pipeline_started(project, plan)` with
  `PlannedStage` entries, `stage_started/completed/skipped`, `side_task_*`,
  `pipeline_completed/failed`) emitted by `WorkflowRunner`, `SideTaskManager`,
  and `delivery.py`. **Test fakes must subclass `NoopProgressReporter`.**
  `create_progress_reporter` (Rich) now serves only `package`/`noise` and
  non-TTY runs.
- `services/tui/` — full-screen Textual dashboard, the default interactive
  experience for `process` (non-TTY falls back to plain logs; no opt-out flag).
  `state.py` is a lock-guarded model mutated by `reporter.py`
  (`TuiProgressReporter`, `owns_screen=True`) from the pipeline **worker
  thread**; `app.py` renders it on a timer on the main thread
  (`run_process_ui` in `__init__.py` owns the lifecycle; abort = double-q,
  relies on stage resumability). Log lines and bars are attributed to stages
  by loguru/thread name (`date-research*`/`cover*` → side tasks). yt-dlp
  switches from its native stdout renderer to `progress_hooks` only when
  `reporter.owns_screen` is set.

## Settings & configuration (`settings.py`)

Pydantic-settings, loaded from `.env`. Notable patterns:

- **Per-stage backend selection**: pre-pass, chunk, post-process, and the
  common utility agents (chunk structural fix + broadcast-date research) each
  pick one spec (`agent_prepass_model` / `agent_chunk_model` /
  `agent_postprocess_model` / `agent_common_model`, i.e. `AGENT_*_MODEL` in
  `.env`).
- **`ModelSpec`**: `*_MODEL` is written as `"backend/model"` or
  `"backend/model/effort"` (effort is one of low/medium/high/extra, default
  high) and parsed into `.backend` + `.model` + `.reasoning_effort`. `effort`
  is mapped per client (gemini thinking_level,
  codex model_reasoning_effort, claude effort); backends without an extra-high
  value clamp repo-level `extra` to their highest supported value. The
  `ModelSpecField` annotation uses `NoDecode` so pydantic-settings doesn't
  JSON-decode the shorthand string — any new spec-shaped field needs the same
  annotation.
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
  (import-time sync check), add the stage body under `workflow/stages/`, wire it
  into `workflow/api.py` with `WorkflowRunner`, add path properties on `Project`.
- New model backend → **inference-layer**.
- New translate behavior → **translate-pipeline** (keep chunk-boundary
  determinism intact).
- Refine/glossary/cover/finalize/packaging → **postprocess-and-packaging**.
- Prompt wording → the `.md`, never inline strings.
- New tunable → a `settings.py` field with a `description`; read it at the call
  site, don't thread it through constructors.
