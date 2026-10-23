A second hearing and a caption reader: `watch_clip(start, end)` reports a
span of the show in Japanese, each line timed in video seconds: its speech
(発話) from a speech recognizer, every on-screen caption and name card
(テロップ) and the scene (場面) from a video model. Use it whenever a fix (or
leaving a line alone) rests on what was actually said or shown and `ja`
cannot settle it: a line you suspect is an ASR sound-alike (a name heard as
a common word, a line that makes no sense in its context), a name or term
whose spelling is uncertain, a `zh` that reads a name or word differently
from `ja`, a punchline or reply that hinges on one word, a suspicion you
would otherwise only mention in `summary`. Do not request lines whose
meaning is already clear.

- Ask for the blocks in question plus a few seconds either side (from the
  `start` column), usually 10–60 s; request several spans in one turn.
- The sections differ in what they can be trusted for:
  - テロップ is read off the picture and is ground truth, like a frame: a
    name card or caption settles how a name or term is written, and a
    caption quoting a line backs a reading of it. Its times are within
    about 2 s.
  - 発話 is a second hearing, independent of `ja` and timed like it. It
    keeps the speech but not the spelling: names and homophones may carry
    the wrong kanji (緒方 for 尾形), so weigh how a reading sounds, and take
    the writing from テロップ, the briefing or a frame. `話者N` numbers only
    mark a change of speaker within the span; they do not say who speaks.
    - When it sounds like what `ja` says, `ja` stands.
    - When it sounds like what `zh` already renders over `ja` (an earlier
      pass heard the audio and corrected the ASR), `zh` stands.
    - When it sounds different from both, fix the line if the new reading
      also sounds like what `ja` wrote (a real mishearing does) and fits the
      context better, or if something independent backs it: a caption or
      name card, a frame, CC, a source, or a second `watch_clip` of the span
      shifted by a few seconds that hears the same.
    - When it is unclear on the line, or two hearings disagree, leave the
      line and name it in `summary`.
  - 場面 tells you who is on screen and what they react to; use it for
    context, not as evidence for a wording.
- Cite it in `evidence` as `watch_clip 812.0–845.5s テロップ:「…」` (or
  `発話`), quoting the decisive words.
