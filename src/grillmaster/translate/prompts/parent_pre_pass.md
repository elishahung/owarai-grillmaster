### PARENT-PROJECT PRE-PASS REFERENCE
The user message includes a Pre-Pass JSON briefing produced for the **previous
episode** of this same program. Treat it as authoritative for cross-episode
consistency:

- `characters.name_zh`, `proper_nouns`, `glossary`, and `catchphrases.phrase_zh`
  values from the parent pre-pass MUST be reused verbatim for any entity that
  also appears (or is referenced) in this episode. Do NOT relocalize a name or
  term that the parent has already fixed.
- You MAY add new entries that only appear in this episode. You MAY refine a
  parent entry only if the current audio/images clearly contradict it (e.g.
  parent had an ASR-error name); in that case prefer the corrected form and
  also include the parent spelling as an alias in `proper_nouns`.
- `tone_notes` and `summary` should be written for THIS episode, but stay
  stylistically continuous with the parent (same register, same address habits)
  unless the audio shows the show has shifted.
- `segment_summaries` are episode-local — do not copy from the parent.
