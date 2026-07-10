---
name: postprocess-and-packaging
description: >-
  Optional agent post-processing and deliverable assembly: `services/postprocess/`
  (refine.py, glossary_check.py, cover.py, date_research.py, _srt_guard.py and
  their prompt .md
  files), `services/finalize/` (SRT → styled ASS + Netflix-TC punctuation), and
  `services/package/` (burn-in, cover copy, noise/remix). Read this before
  changing refine, glossary check, cover generation, finalize punctuation or
  ASS styling, or packaging/burn-in behavior.
---

# Post-processing, finalize & packaging

## Post-processing (`services/postprocess/`)

Optional agent passes, each a thin orchestrator over `run_inference` where the
agent reads/writes files in the project dir and we validate afterward.
`_srt_guard.py` validates **structure only** (block count, indexes, timecodes,
non-empty text) — semantic quality is the agent's responsibility via prompts.

- `refine.py` — polish TC subtitles (`AGENT_POSTPROCESS_MODEL`).
- `glossary_check.py` — full-text terminology/factual consistency check after
  refine. Runs when enabled unless `video.cht.glossary_checked.srt` already
  exists, treats Latin/kana blocks only as priority hints, may use web search
  or on-demand frames, and may correct `.pre_pass/pre_pass.json` after
  preserving the original as `.pre_pass/pre_pass.raw.json` (backup happens
  once; the updated pre-pass must still validate against `PrePassResult`). Its
  prompt ends with a required name-form audit (honorific/name-span parity vs
  `video.ja.srt`) as the final defense against dropped honorifics and
  full-name expansion. When `video.official.ja.srt` exists (platform CC, see
  **project-architecture**), the conditional `official_subtitle_reference.md`
  block is appended: CC wording outranks ASR as source evidence. If the SRT or
  pre-pass changed, `glossary_check.md` (the report) must exist; packaging
  copies reports only if present.
- `cover.py` — stylize the poster. **Always Codex** (image generation),
  regardless of the backend settings; effort comes from `AGENT_COMMON_MODEL`.
  Runs async from the workflow
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
