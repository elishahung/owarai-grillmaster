Hearing the audio: `listen(start, end)` has another model transcribe a span
of the show's audio afresh. Use it whenever a fix (or leaving a line alone)
rests on what was actually said and `ja` cannot settle it: a line you
suspect is an ASR sound-alike (a name heard as a common word, a line that
makes no sense in its context), a punchline or reply that hinges on one
word, a suspicion you would otherwise only mention in `summary`. Do not
listen to lines whose meaning is already clear.

- Ask for the blocks in question plus a few seconds either side (from the
  `start` column), usually 10–60 s; request several spans in one turn.
- The transcript is a second hearing, not ground truth: it can mishear
  too, and it carries no speaker labels or timestamps. A reading that
  `listen` and the context (or a frame, or CC) agree on outranks `ja` and
  is enough to fix the line; when `listen` hears what `ja` says, `ja`
  stands.
- Cite it in `evidence` as `listen 812.0–845.5s:「…」`, quoting the
  decisive words.
