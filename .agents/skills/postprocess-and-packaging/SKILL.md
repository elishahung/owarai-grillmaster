---
name: postprocess-and-packaging
description: >-
  Agent post-processing and deliverable assembly under `src/grillmaster/`: `postprocess/`
  (refine + glossary check), `extras/` (cover, date research, titles), `subtitles/`
  (skeleton check, finalize to video.cht.srt/.ass), `package/` (folder, NVENC render, remix,
  pools, inserts) and their glue in `stages/{refine,glossary,finalize,cover,date_research,package}.py`,
  `pipeline/side_tasks.py`, `pipeline/delivery.py`. Read this before changing refine, glossary
  check, cover, date research, titles, finalize punctuation, ASS styling, or packaging.
---

# Post-processing, finalize & packaging

Agent execution (`AgentTask`, outputs, capabilities, MCP tools, sessions) is
**agent-orchestration**; stage registry, ledger, `grill reset` and layout rules
are **project-architecture**; the chat-panel layer is **live-chat**. Domain
modules take explicit inputs and never import `project`/`config`; only
`stages/` binds `ProjectLayout` paths to them.

## Refine and glossary check (`postprocess/`)

Mandatory stages, role `postprocess`. File-writing agents whose `workdir` is
their stage dir; other inputs are absolute paths in the prompt, project root
readable via `add_dirs`. Tools: `get_frames` (into the stage's `frames/`) and
`check_srt` against the pass's input SRT. Prompts (`postprocess/prompts/*.md`)
own semantic rules and get `ProgramRules.instruction_text(<stage>)` appended.

- **Refine** (`RefineInputs`): `work/09_chunks/merged.srt` →
  `work/10_refine/refined.srt` (required) + `report.md` (optional); reads the
  effective briefing.
- **Glossary** (`GlossaryInputs`): `refined.srt` → `work/11_glossary/checked.srt`
  (required), optional `report.md` and `briefing.candidate.json`. Reads the
  pre-pass briefing, `subs/ja.srt`, and `subs/ja.official.srt` if present
  (adds `official_subtitle_reference.md`: CC outranks ASR). The fixed glossary
  (`glossary/fixed_glossary.json` + `.md`) is copied into the workdir and
  removed in `finally`. Latin/kana-run blocks are listed as priority hints
  (`suspect_blocks`; an exact curated `zh` term cancels a flag).
- **Validation** (failure = repair round): `_shared.check_skeleton` →
  `subtitles.structure.check_aligned` (count, indexes, timecodes, non-empty).
  Glossary also needs `report.md` if the SRT or briefing changed, and a
  candidate that parses as `Briefing`.
- **Briefing promotion**: after acceptance `finish_glossary_check` writes the
  candidate to `work/11_glossary/briefing.json` only if it differs from
  `work/08_prepass/briefing.json` (else removes it), then deletes the candidate.
  Downstream reads `layout.effective_briefing()` (glossary copy, else pre-pass).

## Finalize (`subtitles/finalize.py`, `subtitles/ass.py`)

`checked.srt` → `video.cht.ass` + `video.cht.srt` at the project root, sharing
`video.mp4`'s stem so players auto-load them (declared `outputs`; no
`work/12_finalize/`). Deterministic and last: name spacing (effective briefing
names + curated Han/kana-Latin `zh` from the fixed glossary, longest first),
then Netflix-TC punctuation. `subtitles/ass.py` holds canvas, font, margins and
dialogue style, shared with the chat panel; a change hits every package.

## Extras (`extras/`)

Cover and date research are side tasks (`pipeline/side_tasks.py`): start after
their `start_after` stage on a thread, never fail the run, joined on manager
exit even when a stage failed, skipped entirely by `--break-after`; done =
`state.side_tasks.<key>` record.

- **Cover** (after `download`; `--cover` / `[features] cover`; role `image`,
  `requires={IMAGE_GENERATION}`, codex only): `poster.jpg` staged into
  `work/side/cover/`, the agent's `cover.png` moves to the root. Root cover = hit.
- **Date research** (after `metadata`; `--date-research` / `[features]
  date_research`; role `utility`, `WEB_SEARCH`, `workdir=None` = throwaway temp
  dir): verdict cached at `work/side/date_research/result.json` (corrupt = redo);
  `DateResearchRecord` feeds `effective_broadcast_date`. Moot once a date or
  record exists; a cached verdict is applied even when skipped (`_on_skip`).
- **Titles** (package step; role `utility`, `WEB_SEARCH`, `workdir=None`): three
  titles from the effective briefing, cached at `work/package/titles.json`;
  `[features] title_suggestion` gates only generating a missing one; failure warns.

## Packaging (`package/`, `stages/package.py`)

Delivery step, not a stage: no ledger entry, runs every complete run when
`[paths] package` is set, or alone via `grill package <dir|id> [--remix [pool]]
[--chat-layout]`. In a run it comes after the archive move
(`delivery.run_archive`), so with `[paths] archive` set it reads the archived
copy; a failure there raises `ArchivedDeliveryError` naming
`grill package "<archived dir>"`. `grill archive <id>` retries only the move.

`preflight` (in `Pipeline.check`, before any stage): `[paths] package` must
be a directory and the remix/insert pools must exist (program rules only once
the state knows the series/channel).
`_run` order: require `video.mp4`, `video.cht.ass`, `video.cht.srt` → remix pool (`--remix`
wins, else a `remix = true` program uses `package.remix_pool`; a missing pool
fails) and inserts → `require_pools`, `check_replaceable(destination)` (a
locked old deliverable fails now, not after the render) + `plan_remix`
**before any cursor moves** → titles → burn plan → `deliverable_dir` (staged
`<name>.partial`, swapped in only on success). Inside: plain copies first
(`cover.png`|`cover.jpg`, `info.json` = titles + `briefing.prompt_dict()`,
the briefing part omitted with a warning when the project has none, `refine.md`/`glossary_check.md`
if present, inserts), then the render. Destination
`naming.package_destination`: flat `YYMMDD_<id>_<name>`, MAX_PATH-trimmed.

**Burn plan**: dialogue alone, or with translated chat and layout ≠ `none` the
panel at `work/package/chat.ass` plus a `picture` box (scale+pad after the look).
Layers must sit under the video's dir (ffmpeg runs there with relative names).

**Render recipe** (`package/render.py`; every value measured, do not "clean
up"): scale, 0.2° rotate `bilinear=0`, crop 1920x1080, eq/hue,
`noise=c0s=4:c0f=t+u`, canvas, ASS (after rotate, source timestamps), trim,
`setpts=PTS/1.03`, yuv420p @ 29.94. Audio: band-limit, rubberband 1.03, 44.1 kHz
stereo, -54 dB pink bed (`amix=normalize=0`). `h264_nvenc` p4/hq/VBR `-cq 21`,
maxrate 24M, no `-b:v`. The first 3 s are dropped; each output is probed
against usable/1.03 (same length = failed speed-up; short = SMB/VPN truncation).

- **One ffmpeg process, one filtergraph**: video parts (`-vf`, `-an`) and one
  audio pass (`-filter_complex`) are separate processes, muxed by `concat_copy`.
- Audio is never split; video splits into ≤ 3 parts (≥ 120 s, whole-frame
  boundaries). `EncodeLanes`: 3 NVENC sessions + 1 audio lane across all outputs.
- `-copyts -start_at_zero -ss` before `-i`; map by index (`0:v:0`, `0:a:0`),
  never bare `0:v` (cover-art mjpeg streams).

**Remix**: after the lead trim, `round_half_up(duration / 15 min)` segments
(≥ 2, ≥ 60 s) snapped to `video.cht.srt` cue gaps; `N.mp4` = 60 s noise cut
(format-only fit) + content, concat stream-copied; a segment's scratch
(parts, head, target) is deleted once it passes its duration check.

**Pools** (`pools.py`): `<package>/pools/<name>/` with contiguous `001.*`… and
`.cursor.json` `{index, seconds}` under `.cursor.lock`. Draws advance the cursor
**before** use (concurrent runs never share media; a failed run skips its draw).
Noise walks seconds (`reserve_seconds`); inserts rotate files (`next_file`).

**Inserts**: `[[package.inserts]] {pool, output, when = "remix"|"always"}` copies
the next pool file as `<output><ext>`. An insert named by any `[programs.*]
inserts` applies only to those programs. Outputs ≤ 24 chars, not all digits,
not `video|cover|info|refine|glossary_check`.

## Settings and invariants

- `grill.toml`: `[agents.roles] postprocess|utility|image`; `[features]
  cover|date_research|title_suggestion`; `[paths] package|archive`; `[package]
  remix_pool` (default `"default"`), `[[package.inserts]]`;
  `[programs.series|channel."X"]` `remix`, `inserts`, `instruction.{common,refine,glossary}`.
- Agent-written stages discard old output on re-entry: the runner deletes all
  declared `FilesOutput` files before each attempt; the ledger is the only
  completion marker (`grill reset` to redo).
- No stage rewrites another stage's output (hence the glossary briefing copy).
- Read agent SRTs via `core.srt.read_srt_file` (`utf-8-sig`; Codex writes a BOM).
- Extras caches hit on fixed filenames; no hash/digest invalidation.
