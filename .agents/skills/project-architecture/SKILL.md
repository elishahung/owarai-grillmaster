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
  postprocess-and-packaging, live-chat.
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
live_chat | agent_tools
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
`tui` imports only `events` (plus foundation), never stages or domain code.
Breaking a contract fails `poe check`; fix the dependency, not the contract.

## Pipeline (`pipeline/`, `stages/`)

`core/stage_key.py` `StageKey` declaration order **is** execution order, and
`key.number` numbers the work directory (`work/NN_<key>/`), so reordering
members is a layout change. `pipeline/registry.py` holds the only lists:
`STAGES` (must follow `StageKey` order; `Pipeline.__post_init__` asserts it),
`SIDE_TASKS`, `DELIVERY`, combined into `PIPELINE`. Tests build their own
`Pipeline` from fakes (the CLI takes one via `ctx.obj`).

| # | key | writes (besides `work/NN_<key>/`) |
|---|---|---|
| 1 | `metadata` | state: name, broadcast date, `source` (title, series, channel, talents, broadcast_label) |
| 2 | `download` | `poster.jpg`; `work/02_download/{parts/,full.mp4}`; appends new programs to `grill.toml` |
| 3 | `combine` | `video.mp4` (cut to the section, else `full.mp4` moved), `subs/ja.official.srt`; state `section` |
| 4 | `chat_fetch` | `--chat` only (see live-chat) |
| 5 | `audio` | `work/05_audio/audio.ogg` |
| 6 | `asr` | `work/06_asr/asr.json`; adds `asr_cost_usd` (an existing `asr.json` costs nothing) |
| 7 | `transcript` | `subs/ja.srt` |
| 8–11 | `prepass`, `chunks`, `refine`, `glossary` | work dirs only (translate-pipeline, postprocess-and-packaging) |
| 12 | `finalize` | `video.cht.srt`, `video.cht.ass` (root, `video.mp4`'s stem: players auto-load them) |
| 13 | `chat_translate` | `--chat` only: `subs/chat.cht.json` |

Side tasks: `cover` (after `download`), `date_research` (after `metadata`).
Delivery: `package`. Archive is not a step definition; the runner does it
between the stages and delivery.

**Stage API (`stages/base.py`, below `pipeline`):**
- `StageDef(key, label, weight, run, outputs, enabled, on_skip, params,
  clear_state, preflight)`. `run(ctx)` returns optional `StepCompleted` result
  text. `outputs(layout)` = root deliverables it writes (what `grill reset`
  deletes, with the work dir). `clear_state` resets the state fields it writes.
  `params(config)` is a display/ledger snapshot, **never a cache key**.
  `preflight(config, secrets)` raises before any stage runs (missing ASR key).
- `SideTaskDef[T](key, start_after, run, enabled, record, describe, is_done,
  on_skip, …)`: `run` returns a payload; the manager writes the record
  (default `TaskRecord`) under the state lock.
- `DeliveryStepDef(key, run, workdir, enabled, …)`: no ledger, re-runs on every
  complete run.
- `StageContext`: `layout`, `state`, `save()`, `update(fn)` (locked atomic
  save; use it for any state change a side task may race), `config`,
  `secrets`, `config_file`, `options` (`RunOptions`), `agents`
  (`AgentRunner`), `events`, lazily created `workdir`, `session_dir(label)`,
  and the `Externals` seams `ffmpeg`, `ytdlp`, `http`, `speech_to_text` (built
  once by `pipeline.runner.real_externals`; tests pass fakes from
  `tests/fakes.py`).
- Read upstream artifacts through `require(path, produced_by)`; a missing file
  raises `MissingArtifactError` naming the `grill reset --from` to run.
- Stages are plain modules exporting `STAGE` / `TASK` / `STEP`. Shared glue
  (program rules, yt-dlp request, frame tool, source split, `role_params`,
  `tool_params`, `flag_or_feature`, `chat_enabled`) is in `stages/_common.py`.

**Runner (`pipeline/runner.py`):** `run_project` → `Pipeline.check` (options +
preflight of every stage that will run; a rejected run creates nothing) →
`open_project` → per stage: disabled → `StepSkipped(disabled)`; in the ledger
→ `on_skip` + `StepSkipped(already_complete)`; else `execute_step` (the one
step executor: scope, Started/Completed/Failed, per-step token usage) then
`state.mark_done` with an atomic save. `--break-after <key>` stops after that
stage (ran, complete or disabled) and reports the rest as `breakpoint`; side
tasks, archive and delivery are skipped entirely. Then side tasks join, the
archive move runs (`delivery.run_archive`: logs close, the directory moves,
`ProjectLogs.relocate` reopens the same files appending, `StateStore.relocate`
repoints saves), delivery runs on the moved layout, `RunFinished`. A delivery
failure after a move raises `ArchivedDeliveryError` naming `grill package
"<archived dir>"` (the ID no longer resolves locally).
`deliver_project` (`grill package`) runs only the delivery steps.

**Side tasks (`pipeline/side_tasks.py`):** start on a daemon thread with a
copied contextvars context when the loop passes `start_after`; never fail the
run; always joined in the manager's `__exit__` — also when a stage failed,
because the agent cost is already paid (an interrupt waits only
`INTERRUPT_WAIT_S`).

**Reset (`pipeline/reset.py`):** clears ledger entries, `clear_state` fields,
the whole work dir and declared `outputs`; state is saved first so an
interrupted deletion still reruns. `stages_from(key)` backs `--from`. This is
the only sanctioned way to force a re-run.

**Serial (`pipeline/serial.py`):** each project's final directory (archived
one when `[paths] archive` is set) becomes the next `--parent`; the pre-pass
reads `ProjectLayout(parent).effective_briefing()`. Stops at the first failure
and logs the resume command (an `ArchivedDeliveryError` project counts as done,
since re-running its source would start over); emits `BatchItemStarted`.

## Project (`project/`)

- `layout.py` `ProjectLayout` is the **only** place that spells a project
  path (pure calculator, never creates dirs). Root: `project.json`,
  `video.mp4` + `video.cht.{srt,ass}`, `poster.jpg`, `cover.png`, `subs/`
  (`ja.srt`, `ja.official.srt`, `chat.cht.json`), `logs/{run,events}-<ts>`.
  Work: `work/NN_<key>/`, unnumbered `work/side/<task>/` and `work/package/`.
  Agent sessions: `session/`, `session_<label>/`, retries `session.2/`.
  `effective_briefing()` = glossary's briefing if present, else pre-pass's.
  Add a path here, never inline.
- `state.py` `ProjectState`: identity (`id`, persisted `platform`), `source`,
  `section`, `asr_cost_usd` (ElevenLabs is the only metered service), the
  `stages` ledger `dict[StageKey, StageRecord]` (unknown key = validation
  error), `side_tasks` (`cover: TaskRecord`, `date_research:
  DateResearchRecord`). Readers use `effective_broadcast_date`.
- `store.py`: strict load (no old formats), atomic save, `archive_project`
  (staged copy, verify, swap). `naming.py`: deliverable name
  `YYMMDD_<id>_<name>` and archive/package destinations trimmed by
  `core.paths.fit_dir_name`; `PROJECT_INNER_PATH_RESERVE` is derived from the
  layout's deepest paths, so a deeper path raises it automatically.

## Config (`config/`)

- `grill.toml` (git-ignored) is found walking up from cwd, else
  `$GRILL_HOME/grill.toml`; its directory is the root for `projects/`, `.env`
  and relative paths. `AppConfig` (`model.py`) forbids unknown keys
  everywhere. Sections: `paths`, `agents` (+ `roles`: prepass, chunk,
  postprocess, utility, chat→utility, image), `asr`, `translate`, `features`,
  `package` (+ `inserts`), `programs.{series,channel}`.
- Only `cli/` and `pipeline/` read `AppConfig` wholesale; stages pass the
  sections domain code needs as inputs. No global settings object.
- New setting: add a `Field(description=…)` in `model.py`, document it in
  `grill.example.toml`, regenerate `grill.schema.json` with `uv run poe
  schema` (a test asserts it is current).
- `.env` holds secrets only (`Secrets`: `ELEVENLABS_API_KEY`); a missing key
  fails the ASR preflight.
- `programs.py` `ProgramRules`: channel then series entries merged (`common`
  text before stage text; stage keys `prepass`, `chunks`, `refine`,
  `glossary`). `register_program` (download stage) is the only writer of
  `grill.toml`, appending empty entries with `tomlkit`.

## Events, logs, TUI (`events/`, `tui/`)

- Every report is a frozen dataclass in `events/types.py` emitted to an
  `EventSink`; `EventBus` fans out and isolates broken sinks.
  `events/context.py` `stage_scope` / `task_scope` contextvars attribute
  events and loguru records; worker pools propagate them with
  `copy_context()`. Progress bars go through `events.progress.track`.
- Sinks: `ConsoleSink` (non-TTY), `JsonlSink` (`logs/events-<ts>.jsonl`,
  added by the pipeline), `tui.TuiSink`. `pipeline/logs.py` also writes
  `logs/run-<ts>.log`.
- TUI: `PipelineState` is a pure reducer over events; plan, labels and weights
  come from `RunStarted`; the chunk board is sessions named `chunks/…`. Abort
  kills every live child tree (`core.process.kill_all`).

## Infrastructure

- `sources/`: one `SourcePlatform` per platform (`youtube`, `bilibili`,
  `tver`, `abema`) registered in `registry.py`; ID parsing is
  `core/source_id.py`. BiliBili never sends cookies (format list cap); ABEMA
  resets yt-dlp's cached token before downloading. Extras and broadcast dates
  are best-effort. Downloads keep `skip_unavailable_fragments=False` and fail
  on audio gaps (a gap drifts ASR timestamps).
- External processes only via `media/ffmpeg.py` (ffmpeg/ffprobe) or
  `agents/process.py` (agent CLIs); ruff bans `subprocess.*` elsewhere. Both
  tree-kill on timeout and register in `core.process.LIVE_PROCESSES`.
- `asr/`: ElevenLabs client and the ASR-JSON → SRT builder (tuning constants
  are intentional, not settings). `glossary/`: the fixed glossary, a required
  runtime input of pre-pass, glossary check and finalize.
- `core/`: single copies of SRT parsing (`read_srt_file` reads `utf-8-sig`),
  timecodes, atomic writes, JSON artifacts (`load_model` = corrupt is a miss),
  prompt loading (`render_template` demands exact slots), `ModelSpec`,
  `Briefing`, path budgets.

## CLI (`cli/`)

`grill <src> [HINT]` = `grill run`; options `--break-after <key>`, `--parent`,
`--cover`, `--date-research`, `--chat`, `--chat-layout`, `--remix [pool]`,
`--start`, `--to`. Also `serial`, `package <dir|id>`, `archive <id>`,
`reset <id> --from|--only <key>`, `status [<id>]`, `doctor`. Commands import
heavy modules inside the function so `--help` stays fast; a bare `--remix`
is expanded in `cli/args.py`. On a TTY the run goes through
`tui.run_with_tui`, else a `ConsoleSink`.

## Invariants

- Resumable: a stage is complete iff it is in the ledger; re-running resumes.
- A stage writes only its work dir and its declared `outputs` (download's
  `grill.toml` append is the one exception). No stage rewrites another's output.
- Caches hit on fixed filenames and never self-invalidate; `grill reset` is
  the explicit re-run. Never add hash or staleness checks.
- Agent-written stages (refine, glossary) discard old output on re-entry.
- Side tasks join in `finally`; `--break-after` skips side tasks, archive and
  delivery.
- Every agent call goes through `AgentRunner`; only ElevenLabs is metered.
- Windows MAX_PATH 260: generated directory names go through the path budget.
- Agent-written SRTs may carry a BOM; read them with `core.srt.read_srt_file`.
- No compatibility code: old formats fail loudly.

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
