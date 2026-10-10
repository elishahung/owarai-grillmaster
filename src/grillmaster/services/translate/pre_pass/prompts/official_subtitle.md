### OFFICIAL CLOSED CAPTIONS
The user message includes the platform's official closed captions (字幕放送)
for this program.

When official closed captions are present:
- Treat them as a ground-truth transcript of the spoken lines they cover: for
  the WORDING of a covered line they outrank the audio impression, the
  reference images, and the ASR text. Where a CC line overlaps an ASR line,
  the CC wording is correct and the ASR is the fallible copy. Audio remains
  the truth source for tone and for stretches the CC does not cover.
- CC spellings of person names, place names, product names, and other proper
  nouns are authoritative. Use them to build `proper_nouns` ASR-correction
  mappings and to verify `characters` entries.
- Coverage is PARTIAL: broadcasters only caption spoken dialogue, and segments
  driven by on-screen telop/captions often have no CC at all. A gap in the CC
  does not mean nobody is speaking — rely on the reference images and ASR
  there.
- CC timestamps are approximate relative to the ASR timeline (broadcast
  timing, possibly shifted). Match CC lines to ASR blocks by content and rough
  position, not exact timecodes, and never use CC timing to alter the SRT
  scaffold.
