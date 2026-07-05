---
name: translate-pipeline
description: >-
  The two-stage translation package under `services/translate/` — pre-pass
  whole-film analysis (`pre_pass/`), concurrent chunk translation
  (`chunk/chunk_worker.py`), chunking (`chunker.py`, `facade.py`), structural
  validation and agent repair (`validation.py`, `structural_fix.py`), chunk
  caches under `.chunks/`, `pre_pass.json`, and the prompt `.md` templates.
  Read this before changing chunk boundaries, cache behavior, PrePassResult
  schema, chunk validation, or any prompt under `services/translate/`.
---

# Translate package (`services/translate/`)

Two-stage translation orchestrated by `facade.py` (`class Translate`). Both
stages call `Translate._prepare`, which parses the SRT and splits it into
char-balanced chunks **deterministically** (`chunker.split_into_chunks`), so
pre-pass and chunk stages always agree on boundaries even across resumes —
this determinism is load-bearing: the pre-pass segment summaries are keyed by
exact chunk index ranges, and chunk caches are keyed by range.

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
│   └── prompts.py + prompts/   # pre_pass.md, fixed_glossary*.md,
│                               # official_source_metadata.md, parent_pre_pass.md
└── chunk/
    ├── chunk_worker.py    # translate_chunk: cache → infer → validate → fix
    ├── prompts.py + prompts/   # chunk.md, structural_fix.md (audio-conditioned)
    ├── validation.py / validate_chunk.py  # structural validation
    ├── structural_fix.py  # agent self-validating repair (fix_chunk_structure)
    └── normalizer.py      # strips empty speaker-dash lines (nothing more)
```

## Pre-pass (stage 7)

One call over the **whole** film (full SRT + program info + full audio for
gemini backends + 20-40 SRT-start-aligned representative frames + optional
fixed-glossary + optional parent context). Produces a `PrePassResult` briefing:
character roster, proper nouns / ASR-correction dict, catchphrase fixed
translations, tone notes, and per-segment summaries with explicit
`from_index`/`to_index` matching the chunk boundaries. Persisted to
`.pre_pass/pre_pass.json` — the explicit hand-off to the chunk stage.
`proper_nouns` entries must be honorific-free and same-span (alias → that
alias's own rendering, never the full name; alias identity goes in `role_note`)
— chunk workers apply them verbatim, so a violating entry propagates globally.

**`pre_pass.json` never self-invalidates**: once it exists it is reused as-is
(cost 0), regardless of backend/model/prompt changes. To re-run the pre-pass,
delete `.pre_pass/`.

## Chunk translation (stage 8) — `chunk_worker.translate_chunk`

Per chunk, concurrently (semaphore-bounded: `chunk_api_concurrency` for the
network `gemini-api` backend, lower `chunk_agent_concurrency` for agent
backends, each of which spawns a heavy local process). The worker is a
cache-and-repair ladder:

1. **Raw cache** (`chunk_XXXX-YYYY.raw.srt`) keyed on the chunk range only — an
   existing file skips the model call entirely. Caches never self-invalidate:
   changing backend/model/prompt/settings mid-project requires manually
   deleting `.chunks/` (and `.pre_pass/` for the pre-pass).
2. Build the user message: pre-pass briefing (global + this segment's summary)
   + the chunk's frame timestamps + the SRT slice. Call `run_inference`
   (`schema=None`, free-form SRT out) with retries + exponential backoff.
3. **Strict structural validation** (`validate_chunk_structure`): every source
   timecode appears exactly once, no unexpected or duplicate timecodes, block
   count matches, every output block has non-empty translated text.
4. On validation failure, the **agent fix layer** (`fix_chunk_structure`) hands
   raw output + the source skeleton + the error to an agent backend that
   self-validates until it passes. It may translate a genuinely missing block
   from `source.srt` but must preserve the source skeleton and cannot leave
   blank placeholder blocks. Result cached as `chunk_XXXX-YYYY.fixed.srt`.

`facade._translate_chunks_async` gathers all chunks (collecting partial costs
and per-chunk failures into a `TranslationError` summary on failure), runs
`normalizer.normalize_translated_blocks` (which **only** strips empty
speaker-dash lines), then the facade itself **reindexes to contiguous 1..N**
before writing `video.cht.srt`.

## Prompts

Prompt wording lives in the `.md` files; `prompts.py` is assembly logic only.
The pre-pass and chunk instructions have audio-conditioned variants:
`build_*_instruction(has_audio=...)` applies verbatim find/replace pairs to
strip audio references for non-audio backends. When editing `chunk.md` or
`pre_pass.md`, keep the strings the no-audio substitution searches for intact —
`tests/test_translate_prompts.py` asserts they still occur.
