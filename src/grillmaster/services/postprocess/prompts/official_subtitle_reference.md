Official CC reference (present for this project):

- `video.official.ja.srt` — the platform's official closed captions (字幕放送),
  normalized to SRT. Read-only reference; never modify it. Coverage is PARTIAL
  (spoken dialogue only; telop-heavy segments often have none) and its
  timestamps are approximate against the ASR timeline — match lines by content
  and rough position, not exact timecodes.
- Where a CC line covers an utterance, it is the ground-truth source wording:
  prefer it over `video.ja.srt` when they conflict, both for terminology and
  for the name-form audit (judge honorific and name-span parity against the CC
  line when one covers the utterance).
- CC spellings of names and terms are official. Consult the CC as decisive
  local evidence for proper-noun corrections (including `pre_pass.json` fixes)
  before reaching for web search or frames, and cite the CC line in the report
  like any other evidence.
