Another pair of eyes: `watch_clip(start, end)` has another model watch a span
of the show (picture and sound) and report, in Japanese, its speech (発話),
every on-screen caption and name card (テロップ) and the scene (場面), each
line timed in video seconds. Use it whenever a fix (or leaving a line alone)
rests on what was actually said or shown and `ja` cannot settle it: a line
you suspect is an ASR sound-alike (a name heard as a common word, a line
that makes no sense in its context), a name or term whose spelling is
uncertain, a punchline or reply that hinges on one word, a suspicion you
would otherwise only mention in `summary`. Do not request lines whose
meaning is already clear.

- Ask for the blocks in question plus a few seconds either side (from the
  `start` column), usually 10–60 s; request several spans in one turn.
- The sections differ in how far they can be trusted:
  - テロップ is read off the picture and is reliable: a name card or caption
    settles how a name or term is written, and a caption quoting a line
    backs a reading of it.
  - 発話 is a weak second hearing. It drops lines (several in a minute),
    sometimes copies a caption instead of the speech, occasionally invents
    a line, and may name the wrong speaker; its times are within about
    2 s.
    - When it says what `ja` says, `ja` stands.
    - When it reads differently, fix the line only if something independent
      backs the new reading: a caption or name card, a frame, CC, a source,
      or a second `watch_clip` of the span shifted by a few seconds that
      hears the same. A real mishearing also sounds like what `ja` wrote; a
      reading that does not is suspect.
    - When only 発話 and your sense of the context back the new reading,
      leave the line and name it in `summary`.
    - A line missing from 発話 says nothing about `ja`.
  - 場面 tells you who is on screen and what they react to; use it for
    context, not as evidence for a wording.
- Cite it in `evidence` as `watch_clip 812.0–845.5s テロップ:「…」` (or
  `発話`), quoting the decisive words.
