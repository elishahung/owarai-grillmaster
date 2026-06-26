Refine subtitles for this video project.

Goal: medium refinement, not a rewrite. Produce natural Traditional Chinese subtitles from the Japanese source, fixing errors, awkward phrasing, missing translation, and term consistency while preserving the variety-show roast tone.

Files in the current working directory:

- `video.cht.srt` — original Traditional Chinese subtitles to refine.
- `video.ja.srt` — Japanese source-language reference (account for ASR mistakes).
- `.pre_pass/pre_pass.json` — summary, cast, term glossary, segment summaries.
- Optional visual references under `.pre_pass/media/` and `.chunks/media/frames/`.
- An on-demand frame tool (the exact command is appended at the end of this prompt) to extract extra frames from any moment of the video when the pre-sampled ones do not cover what you need to check.

Write scope (strict): you may only create or modify `video.cht.refined.srt` and `.refine/report.md`. Do **not** touch any other file, in particular:

- `project.json` — do not edit, do not flip progress flags, do not touch its contents in any way. The outer Python workflow owns this file and will mark progress after validating your output.
- `video.cht.srt` and `video.ja.srt` — read-only sources.
- `.pre_pass/`, `.chunks/`, `.asr/` — read-only caches.
- `video.mp4`, `audio.ogg`, `poster.jpg`, etc. — unrelated to subtitle refinement.

Do not run scripts that mutate `project.json` (e.g. don't run the project's own Python entrypoints, validators that write back, or any tool that re-saves state). The on-demand frame tool described at the end of this prompt is exempt: it only writes JPEG frames to a temporary directory outside the project, so running it does not violate this write scope.

Rules:

- Do not change SRT indexes or timecodes.
- Do not merge or split blocks.
- Keep the block count identical to `video.cht.srt`.
- Treat `video.cht.srt` as the refinement baseline. Do not retranslate every block from `video.ja.srt`. Consult `video.ja.srt`, `.pre_pass/pre_pass.json`, and optional frames when the Chinese line is awkward, inconsistent, semantically suspicious, or conflicts with recurring terms/context.
- Treat source conflicts as context-dependent: `video.ja.srt` is ASR output and may be wrong, while `video.cht.srt` was translated by an LLM with video/audio input and may sometimes be more accurate than the ASR Japanese. When they disagree, judge by nearby context, `.pre_pass/pre_pass.json`, optional frames, and the overall segment meaning instead of blindly trusting either file.
- Avoid unsupported subject insertion: When comparing against `video.ja.srt`, remove explicit Chinese subjects such as「我 / 你 / 他 / 她 / 我們 / 大家」or specific names if they were added only for smoothness and are not stated or clearly implied by the Japanese source, immediate context, audio, visuals, or `.pre_pass/pre_pass.json`. Prefer natural subjectless Chinese when the actor is ambiguous.
- The refined subtitle text must be Traditional Chinese. Do not leave Japanese in the subtitle text unless it is an intentional proper noun, title, service name, or quoted term that should remain untranslated.
- Use `.pre_pass/pre_pass.json` for `summary`, `characters`, `proper_nouns`, `glossary`, `catchphrases`, `tone_notes`, and `segment_summaries`. Apply `tone_notes` to register/honorific decisions and `catchphrases` to keep recurring jokes phrased identically across blocks.
- Use frames proactively but selectively. If a concrete subtitle edit depends on
  on-screen text, a visible name/title, a prop, a scoreboard, a reaction shot, a
  visual gag, or a conflict between `video.ja.srt` and `video.cht.srt`, fetch the
  exact frame when the pre-sampled images do not cover that timestamp. Do not
  use frames for routine fluency edits that are already settled by text context.
- Prefer editing only text lines inside each block.
- Preserve intentional Japanese address register and honorifics when they are already present in the Traditional Chinese subtitles. Do not remove or flatten suffixes such as `桑`, `醬`, `君`, `大人`, `前輩`, or `後輩` just to make the line sound more localized. Keep the speaker's polite/plain register contrast through word choice, but treat this as a preservation rule, not a reason to over-edit otherwise natural lines.
- Do not force terminology, proper-noun, or name localization when the existing subtitle is not clearly wrong. For program titles, talent names, group names, segment labels, and other proper nouns, when there is no genuinely common Traditional Chinese (Taiwan) rendering, fall back to the `.pre_pass/pre_pass.json` proper_nouns/characters rendering, else an official/common romanized form with fixed casing and spacing; do not default to raw Japanese kana. For example, fix `ギャロップ (Gallop)` or a raw `コロチキ` to `Gallop` / `KoroChiki`.
- Before polishing a line, identify its variety-show function in context: setup, answer, reaction, roast, self-defense, callback, team-name reference, person-name reference, song/title reference, or scoreboard/segment flow. Preserve that function even when a literal translation sounds smoother.
- Treat recurring team names, nicknames, segment labels, challenge names, and running jokes as show-specific terms. Check nearby blocks, `.pre_pass/pre_pass.json`, and the Japanese source before turning them into generic descriptions. For example, a term like `黒帯` may be a team or performer name in context, not a literal martial-arts rank.
- Keep spoken Mandarin/Taiwan Traditional Chinese subtitle rhythm. Prefer natural conversational particles and compact phrasing when the Japanese line is a quick retort, interruption, or defense; avoid over-formal explanations that flatten the variety-show timing.
- When correcting an awkward but context-dependent line, optimize for the intended joke/interaction over word-for-word equivalence. If the line is about a prior on-screen match, quiz team, or segment action, make that relationship explicit enough for viewers to follow.
- Apply only light Traditional Chinese subtitle style cleanup at this refinement stage: do not rewrite a line that is already accurate, natural, and readable.
- Prefer subtitle typography only when it requires language judgment: use `「」` for quoted speech or quoted terms, `『』` for nested quotes, and `《》` for titles of works when a title mark is clearly needed. Do not spend attention on punctuation cleanup that can be handled mechanically later.
- Keep line wrapping readable, not mechanical: use at most two subtitle text lines, keep one line when it fits naturally, and when editing an existing two-line subtitle, break at Chinese phrase boundaries. Prefer a bottom-heavy shape only when there are multiple natural break points; avoid leaving one or two characters, a lone particle, or stray punctuation on a line. For example:

```text
但有一個人，讓我們把原本
陌生的西洋音樂聽得更親近。
```

instead of:

```text
但有一個人，讓我們把原本陌生的西洋音樂
聽得更親近。
```

- Normalize only clearly awkward number style: use half-width Arabic numerals for precise values, dates, times, measurements, scores, rankings, episode/chapter numbers, and money when compactness matters. Use Chinese numerals for short rounded spoken expressions when they read more naturally. Do not mix Arabic and Chinese numerals inside one number phrase.
- For repeated words, reduce mechanical duplication only when the source repeats the same word twice without comedic or emotional force. Preserve repetition when it carries timing, teasing, panic, emphasis, or a running joke.
- Match profanity, teasing, and roast severity without censoring or intensifying it. Prefer compact Taiwan Traditional Chinese phrasing that preserves the original register and variety-show timing.

Review workflow:

- First inspect the overall context: `.pre_pass/pre_pass.json`, the beginning and
  ending subtitles, and any obvious repeated names, teams, segment labels, or
  catchphrases.
- For large SRT files, work in stable index windows of about 500 blocks
  (`1-500`, `501-1000`, etc.). Within each window, compare `video.cht.srt` with
  nearby `video.ja.srt`, the relevant `segment_summaries`, and surrounding
  blocks. Track replacements by original block index and stitch the edited text
  back into the original SRT skeleton; never generate a newly reindexed file.
- In each window, prioritize concrete defects: mistranslation, missing
  translation, leftover Japanese, unsupported inserted subjects, wrong
  speaker/person/team reference, recurring term drift, joke-function loss,
  overly formal phrasing that hurts timing, and awkward line wrapping.
- When a candidate edit is visually grounded and the timestamp is known, use the
  on-demand frame tool before committing the edit if the pre-sampled frames do
  not already settle it.
- After all windows are edited, run a final cross-window pass for recurring
  names, team names, catchphrases, honorific/register choices, title formatting,
  and repeated joke phrasing. Keep this pass conservative: align inconsistent
  renderings, but do not turn the stage into a full retranslation or glossary
  audit.

After writing the refined SRT, also write a concise refinement summary to `.refine/report.md` (the `.refine/` directory already exists). The report must be a Markdown table with these exact columns:

| 字幕編號 | 原譯 | 修改後 | 修改原因 |
| --- | --- | --- | --- |

Pick at most 10 representative rows. When choosing rows, prefer the most important examples covering: term consistency, Japanese-to-Traditional-Chinese translation fixes, ASR/source-reference corrections, meaning reversals, awkward phrasing cleanup, tone preservation, and recurring joke/name consistency. Do not list every small wording change.

If your edits exceed 10 rows, append a short paragraph after the table describing in general what kinds of remaining changes were made (e.g. minor punctuation, particle smoothing, line-break rebalancing) so the reader knows what is not in the table.

Write the table headers and rows in Traditional Chinese.

Final state:

- `video.cht.refined.srt` exists in the current working directory. Block count, indexes, and timecodes must match `video.cht.srt` exactly. Every block's text must be non-empty Traditional Chinese.
- `.refine/report.md` exists with the table described above.

Reply with just the single word `done`. Do not include explanations, summaries, edit lists, file paths, or any other commentary — the report file already covers the substantive changes, the calling workflow ignores your final message, and any extra tokens are wasted.
