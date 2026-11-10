---
name: project-architecture
description: >-
  Orchestration-level architecture of Owarai GrillMaster under
  `src/grillmaster/`: the declarative pipeline (`pipeline/` registry, runner,
  side tasks, delivery, reset, serial), the stage API and stage glue
  (`stages/base.py`, `stages/_common.py`, `stages/<key>.py`), project state and
  paths (`project/`), config (`config/`: grill.toml, .env secrets, programs),
  events and the TUI (`events/`, `tui/`), the CLI (`cli/`), and the foundation
  and infrastructure packages (`core/`, `sources/`, `media/`, `asr/`,
  `glossary/`). Read this before adding or reordering a stage, side task or
  delivery step, changing resumability, the project layout or the ledger,
  adding a setting or command, adding a source platform, or any change that
  spans packages. Deep dives: agent-orchestration, translate-pipeline,
  postprocess-and-packaging, live-chat, telop, notes.
---

# Owarai GrillMaster — Architecture

A single-user CLI: Japanese variety-show video ID/URL → Traditional Chinese
SRT + styled ASS, optionally burned in. No server, queue or database; each
project's state is `projects/<id>/project.json`, and re-running an ID resumes
where it stopped.

## Layering (enforced by `.importlinter`, run by `poe layers`)

Top to bottom; packages on one line are independent of each other:

```
cli
tui | pipeline
stages
translate | postprocess | extras | package
notes
live_chat | agent_tools | telop
sources | asr | subtitles
agents
project | config
media | glossary
core | events
```

Extra contracts: domain and foundation packages (everything except `cli`,
`pipeline`, `stages`, `project`, `config`) never import `project` or `config`
— they take explicit input objects, and only `stages/` binds the layout and
config to them. `project` and `config` build on `core` alone (no `events`).
`tui` imports only `events` and `core`, never stages or domain code.
Breaking a contract fails `poe check`; fix the dependency, not the contract.

## Pipeline (`pipeline/`, `stages/`)

`core/stage_key.py` `StageKey` declaration order **is** execution order, and
`key.number` numbers the work directory (`.work/NN_<key>/`), so reordering
members is a layout change. `pipeline/registry.py` holds the only lists:
`STAGES` (must follow `StageKey` order; `Pipeline.__post_init__` asserts it),
`SIDE_TASKS`, `DELIVERY`, combined into `PIPELINE`. Tests build their own
`Pipeline` from fakes (the CLI takes one via `ctx.obj`).

| # | key | writes (besides `.work/NN_<key>/`) |
|---|---|---|
| 1 | `metadata` | state: name, broadcast date, `source` (title, series, channel, talents, broadcast_label) |
| 2 | `download` | `poster.jpg`; `.work/02_download/{parts/,full.mp4}` (parts of differing formats are re-encoded to HEVC NVENC, else stream-copied); appends new programs to `grill.toml`; state `segments` (compilations) |
| 3 | `combine` | `video.mp4` (cut to the section, else `full.mp4` moved), `.subs/ja.official.srt` (normally none without CC or with multi-part CC; unusable downloaded CC fails); state `section` |
| 4 | `chat_fetch` | `--chat` only (see live-chat) |
| 5 | `audio` | `.work/05_audio/audio.ogg` |
| 6 | `asr` | `.work/06_asr/asr.json` (MAI-Transcribe-2's raw response; its FLAC upload is made and deleted beside it); adds `asr_cost_usd` (an existing `asr.json` costs nothing; a kept pre-MAI Scribe response fails the stage and the transcript, `grill reset <id> --from asr` redoes it); a response with 100+ words stacked on one timestamp (`asr/timing.py`), cached or not, is deleted and fails the stage, so the next run requests a new one |
| 7 | `transcript` | `.subs/ja.srt` + `ja.voiced.srt` (same blocks, diarized `[V<n>] ` tags for the agents) |
| 8–11 | `prepass`, `chunks`, `refine`, `glossary` | work dirs only (translate-pipeline, postprocess-and-packaging) |
| 12 | `proofread` | `--proofread` / `[features] proofread` only: work dir only; adds `watch_clip_cost_usd` (postprocess-and-packaging) |
| 13 | `finalize` | `video.cht.srt` (root, `video.mp4`'s stem: players auto-load it; under `.subs/` they did not) |
| 14 | `chat_translate` | `--chat` only: `.subs/chat.cht.json` |
| 15 | `telop` | telops on only (`applies` per program): `.subs/telop.cht.json` (see telop) |
| 16 | `notes` | notes on only (`applies` per program): `.subs/notes.cht.json` (see notes) |

Side tasks: `cover` (after `download`), `date_research` (after `metadata`;
notes waits for it), `telop_scan` (after `combine`; glossary, proofread,
`telop` and notes wait for it). Delivery: `compose` (root `video.cht.ass`: the
SRT's dialogue + telops + notes, rebuilt every complete run; its
`DeliveryStepDef.outputs`, which any `grill reset` removes), then
`package`. Archive is not a step definition; the runner does it between the
stages and delivery.

**Stage API (`stages/base.py`, below `pipeline`):**
- `StageDef(key, label, weight, run, outputs, enabled, applies, on_skip, params,
  clear_state, preflight, on_reset)`. `run(ctx)` returns optional `StepCompleted` result
  text. `enabled(options, config)`, like side tasks and delivery steps;
  `applies(ctx)` skips an enabled stage as disabled per project (program
  rules), like `SideTaskDef.applies`; once `metadata` passes, the runner
  reports every later stage/side task that is off (`enabled` or `applies`)
  as disabled at once, deduped per run (the plan, built before the program
  is known, shows `applies`-off steps pending).
  `outputs(layout)` = root deliverables it writes (what `grill reset`
  deletes, with the work dir). `clear_state` resets the state fields it writes.
  `params(config)` is a display/ledger snapshot, **never a cache key**.
  `preflight(config, secrets)` raises before any stage runs (missing ASR key).
  `on_reset(layout)` runs on `grill reset` before deletion (combine moves an
  uncut `video.mp4` back to `full.mp4`).
- `SideTaskDef[T](key, start_after, run, enabled, applies, record, describe,
  is_done, on_skip, …)`: `run` returns a payload; the manager writes the record
  (default `TaskRecord`) under the state lock. `applies(ctx)` skips it as
  disabled per project (reported after `metadata`; `on_skip` still runs at
  the start point).
- `DeliveryStepDef(key, run, workdir, outputs, enabled, preflight, …)`: no ledger,
  re-runs on every complete run; `outputs` = root deliverables derived from
  the stages' (`video.cht.ass`), removed by any reset; `preflight(options, config, state|None)`
  runs in `Pipeline.check` (package: root dir and pools).
- `StageContext`: `layout`, `state`, `save()`, `update(fn)` (locked atomic
  save; use it for any state change a side task may race), `config`,
  `secrets`, `config_file`, `options` (`RunOptions`), `agents`
  (`AgentRunner`), `events`, lazily created `workdir`, `session_dir(label)`,
  `side_tasks.wait(key)` (a stage blocks until that side task of this run
  has finished; returns at once when it never started), and the `Externals`
  seams `ffmpeg`, `ytdlp`, `http`, `mai` (built
  once by `pipeline.runner.real_externals`; tests pass fakes from
  `tests/fakes.py`).
- `pipeline.delivery.as_delivery_step(stage)` runs a stage as the only
  delivery step on an existing (even archived) project whatever its ledger
  says, then records it (`grill telop`, `grill notes`; both then run
  `compose`).
- Read upstream artifacts through `require(path, produced_by)`; a missing file
  raises `MissingArtifactError` naming the `grill reset --from` to run.
- Stages are plain modules exporting `STAGE` / `TASK` / `STEP`. Shared glue
  (program rules, yt-dlp request, frame tool, source split, `role_params`,
  `tool_params`, `flag_or_feature`, `chat_enabled`) is in `stages/_common.py`.

**Runner (`pipeline/runner.py`):** `run_project` → `store.project_lock`
(held for the whole run incl. archive and delivery; `deliver_project` and
`grill archive`/`reset` take it too) → `find_project` (via
`store.locate_project`, the local/archived/none resolver of run and reset;
an archived ID raises `ArchivedProjectError` naming `grill package` before
any preflight, a local ID also archived raises `DuplicateProjectError`
naming both) → `Pipeline.check` (options + preflight of every stage and
delivery step that will run; a rejected run creates no project) →
`open_project` → per stage: disabled → `StepSkipped(disabled)`; in the ledger
→ `on_skip` + `StepSkipped(already_complete)`; else `execute_step` (the one
step executor: scope, Started/Completed/Failed, per-step token usage) then
`state.mark_done` with an atomic save. `--break-after <key>` stops after that
stage (ran, complete or disabled) and reports the rest skipped (`breakpoint`,
or their own `disabled`/`already_complete`); side
tasks, archive and delivery are skipped entirely. Then side tasks join, the
archive move runs (`delivery.run_archive`: logs close, since Windows cannot
move a directory with open files; the directory moves; `ProjectLogs.relocate`
reopens the same files appending, in place if the move failed;
`StateStore.relocate` repoints saves), delivery runs on the moved layout,
`RunFinished`. Archive before package is the owner's choice (the reverse was
tried and reverted): package works on the archived copy. A failed move fails
the run before delivery. A delivery failure after a move raises
`ArchivedDeliveryError` naming `grill package "<archived dir>"` (the ID no
longer resolves locally); `ProjectRun` (what
`grill run` and its TUI retry call; `SerialRun` runs one per item) then
retries only `deliver_project`.
`deliver_project` (`grill package`) runs only the delivery steps.

**Side tasks (`pipeline/side_tasks.py`):** start on a daemon thread with a
copied contextvars context when the loop passes `start_after`; never fail the
run (a stage that needs the output waits via `StageContext.side_tasks` and
redoes or skips the work itself); always joined in the manager's `__exit__` — also when a stage failed,
because the agent cost is already paid (an interrupt waits only
`INTERRUPT_WAIT_S`).

**Reset (`pipeline/reset.py`):** clears ledger entries, `clear_state` fields,
the whole work dir and declared `outputs`; state is saved first so an
interrupted deletion still reruns, then `on_reset` hooks run, then files go.
`stages_from(key)` backs `--from`. This is the only sanctioned way to force
a re-run; `grill reset` refuses directories outside `projects/`.

**Serial (`pipeline/serial.py`):** each project's final directory (archived
one when `[paths] archive` is set) becomes the next `--parent`; the pre-pass
reads `ProjectLayout(parent).effective_briefing()`. Stops at the first failure
and logs the resume command (sources as full URLs; an `ArchivedDeliveryError` project counts as done,
since re-running its source would start over, and so does a source already
archived (`ArchivedProjectError`): its archived dir seeds the next item); emits `BatchItemStarted`.

**Split (`pipeline/split.py`, `grill <src> --parts N`):** the source project runs through
`download`, `split_project` cuts `full.mp4` into `<id>p<k>` projects (metadata, download and
combine in the ledger, `section` on the source timeline, `part_of` naming the source, hint plus
a part note), the source project is removed, then `SerialRun` chains the parts. Re-running
resumes (a part's `project.json` is written last). `grill reset` refuses a part's seeded stages,
since nothing can rebuild them. Anything reaching the platform uses `state.origin_source`, never
`state.id`.

**Compilation (`sources/compilation.py`, `pipeline/compilation.py`, `grill <manifest.json>`):**
the mirror of a split: one project (`manifest.id`) joining several videos of one platform. The
manifest becomes `state.compilation` at creation (identity, like `part_of`; a later run with a
different manifest is refused, a bare-ID run resumes). Metadata fetches each entry
(`entries/<NN>.json`; name/title/overrides from the manifest, date = latest entry's); download
fetches each into `parts/<NN>/` (preferring VP9, `ENTRY_FORMAT_SORT`, so the join can stream-copy), writes `state.segments` (its own `clear_state`) before the
join; prompts get them via `SourceContext.segments`. Refused: `--parts`, `--chat`,
`--start/--to`; `date_research` never applies. `grill plan <playlist>` cuts a playlist (flat
listing, in order, least-squares balanced groups of ≥2) into manifests, never overwriting one.

## Project (`project/`)

- `layout.py` `ProjectLayout` is the **only** place that spells a project
  path (pure calculator, never creates dirs). Root: `project.json`,
  `video.mp4` + `video.cht.{srt,ass}`, `poster.jpg`, `cover.png`, `.subs/`
  (`ja.srt`, `ja.voiced.srt`, `ja.official.srt`, `chat.cht.json`, `telop.cht.json`, `notes.cht.json`), `.logs/{run,events}-<ts>`.
  Work: `.work/NN_<key>/`, unnumbered `.work/side/<task>/` and `.work/package/`.
  Non-deliverables live in dot dirs so Plex/Infuse skip their videos;
  `archive_discards()` (`watch_clip` clips and audio) are deleted by `archive_project`.
  Agent sessions: `session/`, `session_<label>/`, retries `session.2/`,
  handoff `session_fix/` (`core.paths.handoff_path`).
  `effective_briefing()` = proofread's, else glossary's, else pre-pass's briefing; a correcting stage reads `briefing_before(itself)`;
  `effective_subtitles()` (finalize's input) = proofread's SRT if present, else
  glossary's.
  Add a path here, never inline.
- `state.py` `ProjectState`: identity (`id`, persisted `platform`), `source`,
  `section`, `asr_cost_usd` / `watch_clip_cost_usd` (MAI; Scribe + Meta: the metered services), the
  `stages` ledger `dict[StageKey, StageRecord]` (unknown key = validation
  error), `side_tasks` (`cover: TaskRecord`, `date_research:
  DateResearchRecord`). Readers use `effective_broadcast_date`. Every state model (`StateModel`) ignores unknown fields, so a removed feature's key never blocks loading a project.
- `store.py`: load (no old formats; unknown fields ignored), atomic save, `archive_project`
  (staged copy, verify, swap; refuses when the ID is already archived under
  any `YY/MM` or `etc/` or the destination holds `project.json`; deletes the local
  `project.json` first so a failed cleanup leaves no project; with
  `[features] infuse_metadata` it then (re)writes the Infuse sidecar
  `video.xml` from the state, `infuse.py`, also for an already archived
  project; a failed write only warns — Infuse ignores embedded MP4 dates),
  `locate_project` (local, else `find_archived` by
  `naming.archived_candidates`, else none; also behind `grill reset`; an
  archive that cannot be listed only warns there, while `archive_project`
  still fails loudly on it).
  `project_lock(projects_root, id)`: OS lock on `projects/.locks/<id>.lock`
  (outside the project, so it never blocks the archive move and also covers
  the archived copy); a second grill process on the same ID fails at once
  with `ProjectBusyError`; a crash releases it. The pipeline's agent runner
  build seals the projects root (`agents.workspace`: an empty `projects/.git`
  that keeps this checkout's `AGENTS.md` and skills out of agent tasks); do
  not delete it. `naming.py`: deliverable name
  `YYMMDD_<id>_<name>`, archived under `<archive>/YY/MM/` (`etc/` when
  undated), packaged flat; both destinations trimmed by
  `core.paths.fit_dir_name` (the `.partial` staging name counts too; a
  component is measured in UTF-8 bytes even on Windows, since the archive may
  be an SMB share on ext4); `PROJECT_INNER_PATH_RESERVE` is derived from the
  layout's deepest paths, so a deeper path raises it automatically.

## Config (`config/`)

- `grill.toml` (git-ignored) is found walking up from cwd, else
  `$GRILL_HOME/grill.toml`; its directory is the root for `projects/`, `.env`
  and relative paths. `AppConfig` (`model.py`) forbids unknown keys
  everywhere. Sections: `paths`, `agents` (+ `roles`: prepass, chunk,
  postprocess, editor→postprocess (proofread + notes), utility, image), `asr`, `watch_clip`, `translate`, `features`,
  `package` (+ `inserts`), `programs.{series,channel}`.
- Only `cli/`, `pipeline/` and `stages/` see the whole `AppConfig`; stages
  pass domain code only the sections or values it needs. No global settings object.
- New setting: add a `Field(description=…)` in `model.py`, document it in
  `grill.example.toml`, regenerate `grill.schema.json` with `uv run poe
  schema` (a test asserts it is current).
- `.env` holds secrets only (`Secrets`: `ELEVENLABS_API_KEY`, `META_API_KEY`,
  `AZURE_SPEECH_KEY`, `AZURE_SPEECH_ENDPOINT`); a missing Azure key fails
  the ASR preflight, a missing Meta or (with `[watch_clip] speech`)
  ElevenLabs key the proofread's (`watch_clip`). The tool server reads the
  `watch_clip` keys again itself (`agent_tools.meta`, `asr.scribe` via
  `core/env_file.py`), from the `.env` path in its manifest.
- `programs.py` `ProgramRules`: channel then series entries merged (`common`
  text before stage text; stage keys `prepass`, `chunks`, `refine`,
  `glossary`, `proofread`, `telop`, `notes`; `remix`, `telop` and
  `notes` are tri-state, series' explicit value over channel's, `None` =
  follow `[features]` (for `remix`, no remix);
  `stages._common.switch_enabled/switch_applies` add the run flag on top,
  `stages/package.py` does it for `remix`). `register_program` (download stage) is the only writer of
  `grill.toml`, appending empty entries with `tomlkit` under
  `grill.toml.lock`, re-reading the file inside it (concurrent processes).

## Events, logs, TUI (`events/`, `tui/`)

- Every report is a frozen dataclass in `events/types.py` emitted to an
  `EventSink`; `EventBus` fans out and isolates broken sinks.
  `events/context.py` `stage_scope` / `task_scope` contextvars attribute
  events and loguru records; worker pools propagate them with
  `copy_context()`. Progress bars go through `events.progress.track`.
- Sinks: `ConsoleSink` (non-TTY), `JsonlSink` (`.logs/events-<ts>.jsonl`,
  added by the pipeline), `tui.TuiSink`. `pipeline/logs.py` also writes
  `.logs/run-<ts>.log`.
- TUI: `PipelineState` is a pure reducer over events; plan, labels and weights
  come from `RunStarted`; the chunk board is sessions named `chunks/…`. A step's
  progress is its first open bar, a later open bar filling one step of it (nested bars). Abort
  kills every live child tree (`core.process.kill_all`).

## Infrastructure

- `sources/`: one `SourcePlatform` per platform (`youtube`, `bilibili`,
  `tver`, `abema`) registered in `registry.py`; ID parsing is
  `core/source_id.py`. BiliBili never sends cookies (format list cap)
  and downloads in `http_chunk_size` ranges (a CDN host answers 567 to a request without Range); ABEMA
  resets yt-dlp's cached token before downloading. Extras and broadcast dates
  are best-effort. A year-less TVer label takes the latest month/day no later
  than availability + 30 days (reruns re-list months later), checked against
  its weekday; inconclusive → `None`, so date research runs. Downloads keep
  `skip_unavailable_fragments=False` and fail on audio gaps (a gap drifts ASR timestamps). Fragmented (HLS) download
  progress counts fragments; yt-dlp's byte estimate starts at 100%.
- External processes only via `media/ffmpeg.py` (ffmpeg/ffprobe),
  `agents/process.py` (agent CLIs) or the Claude SDK (its CLI `adopt_tree`d);
  ruff bans `subprocess.*` elsewhere. The first two spawn
  `core.process.spawn_tree` trees; every tree is killable and registered in
  `LIVE_PROCESSES`; after `kill_all` (`ABORT` latched) no child may start.
- `asr/`: `mai.py` is the ASR (MAI-Transcribe-2: the episode client, the
  response → `model.WordToken`s, `read_response` refusing any other shape);
  `srt_builder.py` takes tokens and knows no provider (a provider's quirk
  goes in its module, e.g. MAI's `drop_backchannels` (a listener's 相槌
  inside one talker's talk never reaches `ja.srt`); its tuning constants
  are intentional, not settings; a block never starts later than its
  speech, an overlap shortens the earlier block, and a block the next one
  would cut to a flash (< 0.35 s) takes it in one line past the cap, which
  refine and finalize's `_fold_turns` fold back to two Chinese lines); `scribe.py` (Scribe v2) only hears
  `watch_clip` spans. Evaluation and how to check a new candidate:
  `references/asr-provider-evaluation.md`.
  `glossary/`: the fixed glossary, a required runtime input of pre-pass,
  glossary check and finalize.
- `core/`: single copies of SRT parsing (`read_srt_file` reads `utf-8-sig`),
  timecodes, atomic writes, `fs.staged_dir` (a failed swap keeps
  `<name>.partial` with a `.complete` marker and raises `SwapError`; a later
  entry refuses such a kept build instead of deleting it, removes an
  unmarked one, and restores a crash's `<name>.old`; `check_replaceable`
  probes both before long work), `fs.exclusive_lock` (OS-level, released
  when the holder dies; the lock file stays), JSON artifacts (`load_model` = corrupt is a miss),
  prompt loading (`render_template` demands exact slots), `ModelSpec`,
  `Briefing`, path budgets.

## CLI (`cli/`)

`grill <src> [HINT]` = `grill run`; options `--break-after <key>`, `--parent [dir]`,
`--cover`, `--date-research`, `--proofread`, `--telop/--no-telop`, `--notes/--no-notes`, `--chat`, `--chat-layout`, `--remix [pool]`, `--no-remix`,
`--start`, `--to`, `--parts`. Also `serial`, `plan <playlist>`, `package <dir|id>`, `telop <dir|id>`, `notes <dir|id> [--probe]`, `archive <id>`,
`reset <id> --from|--only <key>`, `status [<id>]`, `doctor`. `package`,
`archive` and `status` resolve an ID locally only (pass an archived
project's directory). Commands import
heavy modules inside the function so `--help` stays fast; a bare `--remix`
or `--parent` is expanded in `cli/args.py` (`expand_bare_options`). A bare
`--parent` (run, serial; TTY only) becomes the hidden `--pick-parent`:
`cli/parent.py` lists `store.recent_archived` (latest ledger `completed_at`
within 10 days) in the searchable `tui.picker` before the run starts. On a TTY the run goes through
`tui.run_with_tui`, else a `ConsoleSink`.

## Invariants

- Resumable: a stage is complete iff it is in the ledger; re-running resumes.
- A stage writes only its work dir and its declared `outputs`. Exceptions:
  download appends to `grill.toml`; an uncut combine moves download's
  `full.mp4` to `video.mp4` (`on_reset` moves it back). No stage rewrites
  another's output.
- Caches hit on fixed filenames and never self-invalidate; `grill reset` is
  the explicit re-run. Never add hash or staleness checks.
- Agent-written stages (refine, glossary) discard old output on re-entry.
- Side tasks join in `finally`; `--break-after` skips side tasks, archive and
  delivery.
- Every agent call goes through `AgentRunner`; only ASR (MAI) and
  `watch_clip` (Scribe + Meta) are metered, each with its cumulative state
  field.
- Windows MAX_PATH 260 (ffmpeg and yt-dlp are not long-path aware): generated
  directory names go through the path budget.
- Agent-written SRTs may carry a BOM; read them with `core.srt.read_srt_file`.
- No compatibility code: old formats fail loudly (old archives were migrated
  once; no reader for an old layout or format exists, and none is added).

## Making a change

- New stage: add the `StageKey` member in order (renumbers later work dirs),
  write `stages/<key>.py` exporting `STAGE`, add it to `STAGES`, add layout
  paths, declare `outputs` / `clear_state`.
- New side task: `SideTaskKey`, `stages/<key>.py` `TASK`, a `SideTasks` field,
  add it to `SIDE_TASKS`; `start_after` must name a registered stage.
- New delivery step: `stages/<key>.py` exporting a `DeliveryStepDef`, add it
  to `DELIVERY` (runs after the archive move, on the moved layout).
- New platform: `sources/` + `core/source_id.py` only.
- New backend, capability or agent tool: agent-orchestration.
- Done means `uv run poe check` (fmt-check, ruff, basedpyright, import-linter,
  pytest) passes. Tests mirror `src/grillmaster/`, inject fakes instead of
  patching module paths, and use `tmp_path`; `-m live` tests spend quota.
- Conventions: a package's errors share a `<Package>Error` base (in its
  `errors.py` when it has several); pydantic for anything persisted, crossing
  a process or returned by an agent, frozen slots dataclasses for in-memory
  value objects.
