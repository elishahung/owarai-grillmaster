A second hearing: `transcribe(start, end)` has another model watch a span of
the show (picture and sound) and transcribe it afresh; the captions and name
cards it sees help it spell names. Use it whenever a fix (or leaving a line
alone) rests on what was actually said and `ja` cannot settle it: a line you
suspect is an ASR sound-alike (a name heard as a common word, a line that
makes no sense in its context), a punchline or reply that hinges on one
word, a suspicion you would otherwise only mention in `summary`. Do not
request lines whose meaning is already clear.

- Ask for the blocks in question plus a few seconds either side (from the
  `start` column), usually 10–60 s; request several spans in one turn.
- The transcript is a second hearing, not ground truth: it mishears too,
  most on shouted, overlapping or unclear lines, and it carries no speaker
  labels or timestamps.
  - When it says what `ja` says, `ja` stands.
  - When it reads differently, fix the line only if something independent
    backs the new reading: a frame or caption, CC, a source, or a second
    `transcribe` of the span shifted by a few seconds that reads the same.
    A real mishearing also sounds like what `ja` wrote; a reading that does
    not is suspect.
  - When two hearings disagree, or only one transcript and your sense of
    the context back the new reading, leave the line and name it in
    `summary`.
- Cite it in `evidence` as `transcribe 812.0–845.5s:「…」`, quoting the
  decisive words.
