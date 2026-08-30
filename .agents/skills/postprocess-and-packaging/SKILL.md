---
name: postprocess-and-packaging
description: >-
  Optional agent post-processing and deliverable assembly: `services/postprocess/`
  (refine.py, glossary_check.py, cover.py, date_research.py, _srt_guard.py and
  their prompt .md
  files), `services/finalize/` (SRT → styled ASS + Netflix-TC punctuation), and
  `services/package/` (burn-in, cover copy, titles, noise/remix). Read this before
  changing refine, glossary check, cover generation, finalize punctuation or
  ASS styling, or packaging/burn-in behavior.
---

# Post-processing, finalize & packaging

## Post-processing (`services/postprocess/`)

Optional agent passes, each a thin orchestrator over `run_inference` where the
agent reads/writes files in the project dir and we validate afterward.
`_srt_guard.py` validates **structure only** (block count, indexes, timecodes,
non-empty text) — semantic quality is the agent's responsibility via prompts.
Agent-written SRTs may carry a UTF-8 BOM (Codex does this); every reader of
`video.cht.refined.srt` / `video.cht.glossary_checked.srt` — including
finalize — must read with `utf-8-sig`.

- `refine.py` — polish TC subtitles (`AGENT_POSTPROCESS_MODEL`).
- **Resume rule for both SRT passes**: the progress flag in `project.json` is
  the only completion marker, so entering the stage means the last attempt
  died (timeout/crash/failed validation). An already-present output SRT is
  therefore deleted and the agent re-runs — never reuse it on file existence.
- `glossary_check.py` — full-text terminology/factual consistency check after
  refine. Treats Latin/kana blocks only as priority hints, may use web search
  or on-demand frames, and may correct `.pre_pass/pre_pass.json` after
  preserving the original as `.pre_pass/pre_pass.raw.json` (backup happens
  once; the updated pre-pass must still validate against `PrePassResult`). Its
  prompt ends with a required name-form audit (honorific/name-span/subject
  parity vs `video.ja.srt`) as the final defense against dropped honorifics,
  full-name expansion, and invented subjects (refine also mandates the
  subject check per window). When `video.official.ja.srt` exists (platform CC, see
  **project-architecture**), the conditional `official_subtitle_reference.md`
  block is appended: CC wording outranks ASR as source evidence. If the SRT or
  pre-pass changed, `glossary_check.md` (the report) must exist; packaging
  copies reports only if present.
- `cover.py` — stylize the poster. **Always Codex** (image generation),
  regardless of the backend settings; effort comes from `AGENT_COMMON_MODEL`.
  The cover prompt (`prompts/cover.md`) redraws the poster as a
  Rick-and-Morty-like cartoon and strips text cards, logos, and lettering
  so only the pictured scene remains. Runs async from the workflow
  (see **project-architecture** for the ThreadPoolExecutor/join rules).
- `date_research.py` — broadcast-date web research fallback
  (`AGENT_COMMON_MODEL`, called with `web_search=True` and `cwd=None`
  so the agent gets a throwaway temp dir, never the project dir). Unlike the
  others the agent writes no files: it returns schema-validated JSON
  (`DateResearchResult`: status/date/trust tier/sources), Python persists it
  to `.artifacts/date_research.json` (fixed-filename cache — a parseable file
  skips the agent, a corrupt one counts as a miss and is overwritten; delete
  to re-run) and `apply_date_research_result` writes `Project.broadcast_date`
  on the main thread at join time (below-high trust is adopted with a
  warning). Runs async from the workflow like cover.

`__init__.py` uses lazy `__getattr__` imports so importing the package doesn't
drag in every backend.

## Finalize (`services/finalize/`)

SRT → styled ASS + cleaned SRT. Two jobs, not one:

- **Netflix-TC punctuation rules** (strip terminal commas/periods, collapse
  ellipses, convert mid-line `。`→`，`, etc.). The ASS style header lives here.
- **Mixed-script name spacing**: pulls Latin-containing names from
  `pre_pass.json` (proper nouns + characters) **and** from the fixed glossary
  (`_curated_name_units`, longest-first to avoid substring collisions) and
  applies deterministic spacing. So `services/fixed_glossary/` is a runtime
  input here, not just prompt content.

Input precedence (enforced in `workflow.py`, not here): glossary-checked SRT →
refined SRT → translated SRT — first that exists wins.

## Packaging (`services/package/`)

Post-loop deliverable assembly (not a pipeline stage): burn ASS into the video
(`core.py`), copy cover and analysis artifacts (`pre_pass.json`, optional
`refine.md`/`glossary_check.md` — silently skipped when absent), plus a
`noise`/`remix` packaging path (`noise.py`, `remix.py`). Burn-in itself lives
in `services/media.py` (duration-validated; see **project-architecture**).

`titles.py` is the only agent call inside packaging: three TC title candidates
derived from the source project's `pre_pass.json` (`AGENT_COMMON_MODEL`,
`schema=TitleSuggestions`, `cwd=None`), cached at `.titles/titles.json` in the
**source** project (fixed-filename cache; corrupt counts as a miss) and copied
into the deliverable as `titles.json`. `ENABLE_PACKAGE_TITLE_SUGGESTION` gates
only generating a missing file — an existing one is always reused and copied.
Best-effort like the rest of packaging: a failure warns and packaging continues.
Default package and remix **content** segments share one look recipe
there (`_PACKAGE_VIDEO_FILTER` / `_PACKAGE_AUDIO_FILTER` /
`_PACKAGE_ENCODE_ARGS`): ASS first (when present), then scale, a 0.2°
rotate (`PACKAGE_ROTATE_RADIANS` repeated on `a`/`rotw`/`roth`), crop,
tempo `PACKAGE_TEMPO` (1.03, via `setpts` + `rubberband`), grade/noise, 1920x1080
yuv420p @ 29.94 fps, 44100 stereo, plus a -42 dB white-noise bed
(`anoisesrc` `a=0.008` mixed with `amix=normalize=0` so program level is
unchanged). Video encode is `h264_nvenc` (p5 / hq / VBR CQ 19); the CPU
filter graph (libass, lanczos, `noise`, `eq`/`hue`, rubberband) stays
software — there is no CUDA equivalent for those looks.
Default package drops the first `PACKAGE_LEAD_TRIM_SECONDS` (3 s) after
ASS burn and before tempo; remix does the same only on the first content
segment, then wraps noise around that already-trimmed clip. Expected
duration is `usable / PACKAGE_TEMPO` — a same-length output on a long show
is a failed speed-up, not a success. There is no noise prep step:
`noise/<name>/` holds raw source videos named `000.*`, `001.*` … (contiguous
three-digit stems, any container), and `noise.py` walks them **in seconds**
with a `state.json` cursor `{next_index, next_seconds}` — each cut is
`NOISE_CUT_DURATION_SECONDS` (60 s), a source whose remainder is under one
full cut is consumed to its end in that same cut, and the last index wraps
back to 0. `MediaProcessor.encode_noise_segment` transcodes each reserved cut
at remix time with a format-only fit (`_NOISE_VIDEO_FILTER` /
`_NOISE_AUDIO_FILTER` + the same encode args, no look filters) so remix
concat can still stream-copy video. `reserve_noise_cuts` persists the
advanced cursor **before** rendering, so concurrent packaging runs never draw
the same noise — a run that then fails skips its reserved noise rather than
reusing it. Remix splits near every 8 minutes (`REMIX_SEGMENT_SECONDS`)
on a subtitle gap/boundary, then wraps each segment as noise + content +
noise (`video_1.mp4`, `video_2.mp4`, …).

`rc.py` reads `.packagerc` (git-ignored, at the working-directory root):
`{series|channel: {<name>: {remix?}}}`. The download stage appends empty
entries for names it sees; a hand-set `"remix": true` on the project's series
or channel forces remix packaging with `DEFAULT_NOISE_NAME` ("default") when
`--remix` was not passed — a missing `noise/default` folder then fails the
package instead of degrading to a burn-in. `--remix` without a value means the
same default (expanded from argv in `main.py`; Typer has no optional-value
options).
