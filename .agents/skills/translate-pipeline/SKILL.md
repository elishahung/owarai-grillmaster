---
name: translate-pipeline
description: >-
  Two-step subtitle translation: the domain package `src/grillmaster/translate/`
  (chunker.py, prepass.py, chunk.py, prompt.py, assets.py, inputs.py,
  prompts/*.md), its stage glue `stages/prepass.py` and `stages/chunks.py`
  (plus `split_source` / `source_context` in `stages/_common.py`), and the
  shared contracts `core/briefing.py` and `core/id_coverage.py`. Read this
  before changing chunk boundaries, the Briefing or ChunkTranslation schema,
  chunk validation or caches, frame/audio inputs, or any translate prompt.
---

# Translation (`translate/` + `stages/prepass.py`, `stages/chunks.py`)

`subs/ja.srt` (transcript stage) → **prepass** stage: one whole-film agent call
→ `Briefing` → **chunks** stage: concurrent per-chunk agent calls → merged SRT.
Runner, adapters, `AgentTask`/`AgentJob` and repair rounds: **agent-orchestration**.
Stage registry, ledger, `grill reset`, layout: **project-architecture**.

## Ownership

- `translate/` is pure domain code: it never imports `project` or `config`.
  `stages/` binds layout/state/config into the frozen inputs in `inputs.py`:
  `SourceContext` (title, description, hint, talents, `program_instruction`
  for the running stage, `official_subtitles` CC blocks, `parent_briefing`),
  `PrepassInputs`, `ChunkInputs` (one call), `ChunkBatchInputs` (whole run).
- `chunker.split_into_chunks` (char-balanced, blocks never split);
  `prepass.py` builds/validates the pre-pass task and caches the briefing;
  `chunk.py` owns `ChunkTranslation`, its validator, the batch run and the
  rebuild/merge; `prompt.py` only assembles prompts; `assets.py` samples
  frames and slices audio for both steps.
- `core/briefing.py` owns `Briefing`; `core/id_coverage.py` the id coverage
  check shared with live-chat batches.

## Artifacts (`project/layout.py` is the only path authority)

- `work/08_prepass/briefing.json` (+ `frames/`, `session/`).
- `work/09_chunks/<from>-<to>/` (`chunk_range_name`, `0001-0119`): `frames/`,
  `audio.ogg`, `translation.json`, `session/`; the dir is also the agent cwd.
- `work/09_chunks/merged.srt` — renumbered 1..N on Japanese timecodes (refine input).
- The chunks stage and a serial parent's briefing read
  `layout.effective_briefing()` (the glossary stage's copy wins when present).

## Invariants

- **Chunk-boundary determinism.** Both stages call `stages._common.split_source`
  → the same `split_into_chunks` on the same SRT and limit. Segment summaries
  and chunk caches are keyed by exact index ranges; never chunk elsewhere.
- **Caches hit on fixed filename existence, never self-invalidate.**
  Briefing, translations, stills and audio slices are reused regardless of
  model/prompt/config changes (stage `params` are display-only); re-run with
  `grill reset <id> --from|--only`. A cached `translation.json` that no longer
  covers its chunk fails loudly (validated once on read).
- **All agent calls go through `AgentRunner`**: `run` for the pre-pass,
  `run_jobs` for chunks (`[agents] max_concurrent`). Each job's `accept`
  writes `translation.json` at once, so finished chunks survive failures and
  Ctrl-C; failures raise one `TranslateError` after the batch.
- **Python owns timecodes.** The agent returns `{blocks: [{index, text}]}`
  only; `rebuild_blocks` puts cleaned text under each source block's index and
  timecode. `clean_text` drops blank and dash-only (`-`) lines; validation
  judges the cleaned text, so a debris-only block is "empty".
- `ja.srt` stays the sole block/timecode scaffold; official CC is a wording
  reference only (pre-pass gets all of it, a chunk the blocks overlapping its
  time range ±2 s).

## Contracts

- `Briefing` (strict JSON, all fields required, no free-key maps): `summary`,
  `characters[{name_jp,name_zh,role_note}]`, `proper_nouns` and `glossary` as
  `TermMapping{source,target}` lists (conflicting targets for one source are
  rejected), `catchphrases[{phrase_jp,phrase_zh,note}]`, `tone_notes`,
  `segment_summaries[{from_index,to_index,summary}]`. Prompts see
  `render_for_prompt()`: term lists folded to `{source: target}`; with
  `chunk=` the list becomes that range's single `segment_summary`.
- Pre-pass: `Role.PREPASS`, `SchemaOutput(Briefing)`, requires `WEB_SEARCH`;
  `segment_coverage_validator` demands one summary per chunk range (repair
  names the missing ranges); the boundary block stays last in the message.
- Chunk: `Role.CHUNK`, `SchemaOutput(ChunkTranslation)`, `attempts =
  chunk_attempts`; `chunk_validator` reports missing/duplicate/unknown/empty
  indexes via `id_coverage`.
- Media: pre-pass gets one still per `prepass_frame_interval_s` of SRT
  (clamped to 20-40 and the block count) at block starts (+0.2 s), a chunk one
  per `chunk_frame_interval_s` of its span; audio (full track / chunk slice)
  only when `accepts_audio(role)` reports `AUDIO_INPUT`. `get_frames` is
  scoped to 0..last block end (pre-pass) or the chunk's time range.

## Prompts (`translate/prompts/*.md`; wording lives only in `.md`)

- Audio variants are fragments: `pre_pass.md` / `chunk.md` mark audio spans
  with `{{audio:<name>}}`; `<stem>_audio.md` and `<stem>_no_audio.md` each hold
  one `<!-- <name> -->` section per slot. Both must define exactly the
  template's slots or rendering raises — add/rename slots in all three files.
- Pre-pass instruction order: audio-rendered `pre_pass.md`, then conditional
  `official_source_metadata.md` (talents), `official_subtitle.md` (CC),
  `fixed_glossary.md`, `parent_pre_pass.md` (parent), program instruction,
  `frames_guidance(pre_pass_frames.md)`, `pre_pass_web_search.md`.
  Chunk instruction: audio-rendered `chunk.md`, program instruction,
  `frames_guidance()`.
- `core/prompts.py`: `load_prompt`, strict-slot `render_template`,
  `render_program_instruction`, `frames_guidance` (shared
  `core/prompts/frames_tool.md` + stage fragment). User-message headings are
  `PromptSection` constants shared by both steps.

## Config (`grill.toml`, `config/model.py`)

- `[agents.roles] prepass`, `chunk` — `backend/model[/effort]`.
- `[translate]`: `chunk_char_limit` (6000; changing it moves every boundary),
  `chunk_attempts` (3), `prepass_frame_interval_s` (60),
  `chunk_frame_interval_s` (30), `frame_max_side` (768).
- `[programs.series|channel."<name>".instruction]` keys `common`, `prepass`,
  `chunks` reach the prompts as `SourceContext.program_instruction`.
