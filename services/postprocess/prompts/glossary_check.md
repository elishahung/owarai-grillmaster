Glossary-check the refined subtitles for this video project.

Goal: a full-text terminology and factual-consistency pass. This is not the
refinement pass: do not polish style broadly, do not rewrite lines just because
they could be smoother, and do not retranslate the whole file. Focus on names,
groups, titles, catchphrases, show-specific terms, factual grounding, glossary
mismatches, and corrections to the shared pre-pass briefing when evidence shows
it is wrong.

Files in the current working directory:

- `video.cht.refined.srt` — the refined Traditional Chinese subtitles; the
  baseline you copy from.
- `video.cht.glossary_checked.srt` — your subtitle output.
- `.glossary_check/fixed_glossary.json` — the authoritative curated jp->zh fixed
  glossary.
- `.glossary_check/fixed_glossary.md` — fixed-glossary translation philosophy
  for terms not present in the json.
- `.pre_pass/pre_pass.json` — summary, cast, proper_nouns, glossary,
  catchphrases, tone notes, and segment summaries. You may correct this file
  only when you verify it is wrong.
- `.pre_pass/pre_pass.raw.json` — original pre-pass backup created by the Python
  workflow before you run. Read it if useful; do not modify it.
- `video.ja.srt` — ASR Japanese source. It may be wrong, but it is useful when
  a Chinese term looks suspicious and you need to recover the source wording.

Procedure:

- First copy `video.cht.refined.srt` to `video.cht.glossary_checked.srt`
  verbatim (a plain file copy, not a re-emit).
- Review the entire subtitle text for suspicious terminology, names, groups,
  titles, catchphrases, factual references, or inconsistent renderings. Treat
  the priority suspect list below as a starting point, not the full scope.
- Edit only spans with a concrete reason. Most blocks should remain
  byte-identical to the copied file.
- If `.pre_pass/pre_pass.json` contains a verified wrong name, term,
  catchphrase, character entry, or segment summary, update the JSON in place
  while preserving its schema.

Write scope:

- You may create or modify only:
  - `video.cht.glossary_checked.srt`
  - `.glossary_check/report.md`
  - `.pre_pass/pre_pass.json`
- Do not modify `.pre_pass/pre_pass.raw.json`; it is the immutable backup.
- Do not touch `project.json`. The outer Python workflow owns progress flags.
- Do not modify `video.cht.refined.srt`, `video.ja.srt`, fixed glossary files,
  media files, or unrelated cache directories.
- Do not run scripts that mutate `project.json` or re-run pipeline stages.

Evidence rules:

- Use local context first: fixed glossary, `pre_pass.json`, nearby subtitle
  blocks, source ASR, and existing project metadata.
- Use built-in web search when local context is insufficient for an external
  fact: official name spellings, talent/group names, program titles, segment
  titles, or public references.
- Use the frame extraction tool when visual ground truth may settle the issue:
  on-screen text cards, lower-thirds, captions, props, labels, scoreboards,
  costumes, visible names, or visual gags at a specific timestamp.
- If you use web search or frames to make a correction, record that evidence in
  `.glossary_check/report.md`.
- Do not guess. If evidence is inconclusive, leave the subtitle and pre-pass
  entry unchanged.

Subtitle rules:

- Do not change SRT indexes or timecodes. Do not merge or split blocks. Keep the
  block count identical to `video.cht.refined.srt`.
- Do not re-output, reformat, or re-wrap the whole file. Edit only the justified
  spans in the copied file.
- Preserve tone, address register, and honorific suffixes already present unless
  the term itself is objectively wrong.
- Matching glossary entries is by meaning in context, not exact source-string
  equality. Json aliases are Japanese source spellings, while the subtitle text
  may already be Chinese or romanized.
- If a flagged token maps to `fixed_glossary.json`, render only the span the
  token actually covers. Do not expand partial names to a full entry. Example:
  source `徳井` -> `德井`, never `德井義實`; `Zakoshisyoh` alone -> `雜魚師匠`,
  not `好萊塢雜魚師匠`.
- Intentional proper nouns, official romanized names, service names, titles, or
  quoted terms may legitimately stay non-Chinese.

Pre-pass rules:

- Keep `.pre_pass/pre_pass.json` valid for the existing schema:
  `summary`, `characters`, `proper_nouns`, `glossary`, `catchphrases`,
  `tone_notes`, and `segment_summaries`.
- Preserve segment boundaries. Do not add, remove, or renumber
  `segment_summaries`; only correct their text when evidence proves a summary
  or named entity is wrong.
- Keep corrections minimal and aligned with the subtitle decisions. The purpose
  is to fix downstream shared terminology, not to regenerate pre-pass.

Report:

If you changed `video.cht.glossary_checked.srt` or `.pre_pass/pre_pass.json`,
write `.glossary_check/report.md` as a compact Traditional Chinese debug note,
not a formal audit table. Group repeated subtitle edits by correction, mention
representative block numbers, and include the decisive evidence (frame filename,
web source, or `video.ja.srt` line) only where it mattered. If `pre_pass.json`
changed, add a short `Pre-pass corrections` section listing only changed fields
as `field: old -> new; reason/evidence`. If you changed nothing, do not create
the report.

Final state:

- `video.cht.glossary_checked.srt` exists in the current working directory.
- Its block count, indexes, and timecodes match `video.cht.refined.srt`
  exactly.
- `.pre_pass/pre_pass.json` is valid JSON matching the existing pre-pass schema.
- `.pre_pass/pre_pass.raw.json` is untouched.
- `.glossary_check/report.md` exists if and only if subtitles or pre-pass were
  changed, and it names any web/frame evidence used for corrections.

Reply with just the single word `done`. Do not include explanations, summaries,
edit lists, file paths, or any other commentary.
