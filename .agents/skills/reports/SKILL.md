---
name: reports
description: >-
  Measurement reports under `docs/reports/`: model comparisons, A/B runs,
  archive statistics, cost or quota estimates. Covers where the data lives
  (archived `project.json` and `.work/*/session/result.json`), how to measure
  without misleading yourself (coverage, confounders, rerun variance), the
  report layout, and what must stay out of a public report (local paths,
  personal schedule, account details). Read this before measuring across
  episodes or writing or editing a report.
---

# Measurement reports

A report records a measurement so a later decision can cite it. Skills keep
only the rule a report changes and link to the report; the numbers stay in the
report.

## Where the data is

The archive root is `[paths] archive` in `grill.toml`; episodes sit at
`<archive>/<yy>/<mm>/<episode>/`. Read the archived copy, not `projects/`,
which holds only work in progress.

- `project.json` → `stages.<key>`: `completed_at`, `elapsed_s`, `agent_usage`
  (token counts), `params.model` (`backend/model/effort`). Also
  `asr_cost_usd`, `watch_clip_cost_usd`, `section`, `parent`.
- `.work/NN_<key>/…/session/result.json`: per-call backend, model, effort,
  timing and usage (one per chunk under `09_chunks/<range>/`).
- Stage outputs for content comparisons, e.g. `09_chunks/merged.srt` against
  `10_refine/refined.srt`.
- Episode length: ffprobe the archived video; there is no duration field.

Write throwaway analysis scripts in the session scratchpad, not the repo, and
describe the method in the report instead.

## Measuring without misleading yourself

- **Cover every episode in the window.** Count all projects the question
  covers; when an older record has a different shape, migrate or adapt the
  reader rather than skipping it. State the sample size and anything that could
  not be measured.
- **Say what actually ran.** Stage models changed over time, and a timestamp
  such as `chunks.completed_at` moves when an episode is reset and rerun. When
  the report extrapolates (for example "this volume, if run on model X"), say
  which models produced the data and that the figure is a projection.
- **Hold the other stages fixed.** A downstream metric reflects every model
  in front of and behind it; the refine change rate, for instance, moves with
  the refine model. Compare only within matching conditions and list the ones
  that differ.
- **Measure rerun variance first.** The same model rerun on the same chunk
  differs about as much as two models do, so wording diffs are not evidence.
  Judge quality against a fixed list of known errors. The low-quota A/B recipe
  (isolated `GRILL_HOME`, delete one chunk's outputs, `--break-after`) is in
  [the 2026-11-21 model comparison](../../../docs/reports/2026-11-21_translate-model-comparison.md),
  section 4.
- **Agent and metered calls need the owner's go-ahead.** Archive statistics
  are free; any probe, live test or A/B rerun spends subscription quota or
  money. Ask first, and record the calls made and USD spent in the report.
- **Name the assumptions.** A single-episode baseline, linear scaling by
  duration, or a vendor multiplier taken on trust each go in the limits
  section.

## Report layout

File: `docs/reports/YYYY-MM-DD_<topic>.md` (kebab-case topic). Write in
Traditional Chinese, plain sentences, terms as the code spells them (stage
keys, model ids in backticks).

1. Title stating the question, then `日期：YYYY-MM-DD`.
2. `## 結論`: the answer in a few bold-led bullets, each with its key number.
3. Data sections: tables with the window, sample size and units in the
   heading or the sentence before them. Absolute dates, not "last week".
4. `## 方法`: data sources by field or relative path, the time window, how
   episodes were paired or filtered, calls and cost spent.
5. `## 前提與限制` (or `### 限制` under 方法): sample size, assumptions,
   confounders.

Commit as `docs(reports): <summary>` when asked to commit.

## Keep it public-safe

The repo is public. Before finishing, read the report for:

- absolute or machine paths: drive letters, user folders, NAS share names,
  the archive location. Use `<archive>/…`, `projects/<id>/…` or `.work/…`
  instead.
- the owner's schedule or habits: clock times or "early morning" attached to
  when they ran things. Dates alone are fine.
- account details: emails, usernames, API keys or endpoints from `.env`.

Show and episode titles, platform ids and model names are fine.
