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
- The transcript is a second hearing, not ground truth: it can mishear
  too, and it carries no speaker labels or timestamps. A reading that the
  transcript and the context (or a frame, or CC) agree on outranks `ja` and
  is enough to fix the line; when the transcript says what `ja` says, `ja`
  stands.
- Cite it in `evidence` as `transcribe 812.0–845.5s:「…」`, quoting the
  decisive words.
