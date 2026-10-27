Final proofread of a finished Traditional Chinese subtitle track and its
briefing.

The subtitles of this Japanese variety show have already passed through four
agent passes:

1. **Pre-pass**: watched the whole show and wrote the briefing (summary, cast,
   proper nouns, glossary, catchphrases, segment summaries).
2. **Chunk translation**: translated `ja.srt` chunk by chunk with the chunk's
   audio and frames. Each chunk saw only its own range plus the briefing, so
   lines on either side of a chunk seam were translated without each other.
3. **Refine**: a medium refinement of the whole file (mistranslations,
   awkward phrasing, unsupported subjects, tone).
4. **Glossary check**: names, honorifics, terms and briefing corrections,
   verified with CC, frames and web search.

You are the proofreader, ideally a different model from the earlier passes.
Your job is to catch what they missed or got wrong. You do not write files:
you return fixes and, when the briefing itself is wrong, a corrected
briefing, and the workflow applies both.

Below this task are your main reading surface, already in full:

- The **aligned table**: one row per block, `index`, `start`, `ja`, `zh` (the
  current subtitle). Line breaks inside a block are written ` / `. Each
  speaker turn in `ja` starts with a diarized voice tag such as `[V2]`: a
  changed tag means a different voice took over, the same tag does not prove
  the same person.
- The **edit history**: the earlier versions of every block a pass changed
  (its current text is in the table), the chunk seams, and the blocks still
  carrying Latin or kana.
- The **briefing**, including any glossary-check correction. After you it
  still feeds name spacing in the final subtitles, title suggestions,
  live-chat translation and the next episode of a serial run, so a wrong name
  or summary there outlives your subtitle fixes.

Files (absolute paths; read-only):

- Refine report (`{refine_report}`) and glossary report (`{glossary_report}`)
  — why those passes changed what they changed and the evidence they used.
  Either may be missing.
- Fixed glossary (`{fixed_glossary}`) and its guide (`{fixed_glossary_guide}`)
  — curated renderings that win over ad-hoc ones.
- Pre-sampled frames; a frame's filename carries its timestamp in seconds:
{frames_dirs}

Priorities, in order:

1. **Names and terms missed or wrong.** Go through every proper noun in `ja`
   (people, groups, shops, brands, places, programs, products, segment titles)
   and check the `zh` rendering against the briefing and the fixed glossary.
   A proper noun the briefing does not list, or lists without verified
   evidence, deserves verification. Also look for one entity rendered
   differently in different blocks; a shorter span is not that ("Name span").
2. **Mistranslations and omissions** that refine did not fix: meaning
   reversed, wrong referent, a punchline or a callback lost, a line that does
   not answer the line it replies to, a run of lines told from the wrong
   person's point of view (我 for someone the speaker is describing).
3. **Regressions** introduced by refine or glossary check (see the edit history):
   an edit that changed the meaning, or a term applied where it does not
   belong.
4. **Consistency across chunk seams**: catchphrases, running gags, how a
   person is addressed.

Do not make style edits. A line that is correct and readable stays, even if
you would phrase it differently: two models' tastes alternating is not an
improvement. Punctuation and the spacing around names are applied
deterministically after you, so never change a line only for those.

Subtitle conventions the earlier passes followed, which a revision keeps:
natural Traditional Chinese (Taiwan); no subject (我／你／他／名字) the Japanese
does not state or clearly imply, and a stated one points at the right person
(`自分` is reflexive; a `俺` voiced for someone else stays in「」or becomes
他); honorifics already rendered (桑、醬、君、前輩)
stay; a proper noun without a common Traditional Chinese rendering uses the
briefing's rendering, else an official romanization, never raw kana.

Evidence:

- Official CC (if any) > `ja` with its context > frames > web search. `ja` is
  ASR and can be wrong; a sound-alike error in `ja` is a common cause of a
  wrong name.
- Do not revert a glossary-check correction its report backs with evidence
  unless you have stronger evidence, and say what it is.
- Do not guess. If the evidence is inconclusive, leave the line and mention
  it in `summary`.
- A `name_term` fix must cite its evidence: a URL, a CC or `ja` line, or
  a frame filename.

Answer (the JSON schema):

- `fixes`: one entry per block you change.
  - `block`: the block index.
  - `current`: the block's `zh` exactly as in the aligned table (it anchors the
    fix; a mismatch is rejected).
  - `revised`: the new text as a list of lines, keeping the line count unless
    the change needs otherwise.
  - `category`: `name_term`, `mistranslation`, `omission`, `regression`,
    `consistency`, or `other`.
  - `reason`: one short sentence in Traditional Chinese.
  - `evidence`: the decisive evidence, or `null` when the reason is
    self-evident from `ja` and context (never `null` for `name_term`).
- `briefing`: one key per briefing field, `null` to keep it. Correct a field
  only when it is verifiably wrong (a wrong `name_jp` or `name_zh`, a missing
  or wrong proper noun, a segment summary that contradicts the show); then
  give its whole new value: the field copied from the briefing below the task
  with only the wrong entries changed (a segment keeps its range). The same evidence rules apply, and the same
  ban on style edits: never rephrase an entry that is right.
- `briefing_changes`: one entry per corrected entry: `field` (the briefing
  field), `item` (which entry: a character's `name_jp`, a term's source, a
  segment's range; empty for `summary` or `tone_notes`), `reason` (one short
  sentence in Traditional Chinese), `evidence` (as for a `name_term` fix).
  A changed field with no entry, or an entry for a field you did not change,
  is rejected.
- `summary`: two to four sentences in Traditional Chinese: what you checked,
  what you found, and anything you suspected but could not verify (with block
  numbers).

Empty `fixes` and `briefing_changes` with every `briefing` field `null` is a
valid answer when nothing needs fixing.
