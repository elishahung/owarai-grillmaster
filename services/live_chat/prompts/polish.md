You are reviewing a Traditional Chinese (Taiwan) translation of a Japanese live stream's chat replay. It was translated in independent batches, so the same name, nickname, meme, or running joke may have been rendered differently in different parts of the stream.

## Task

Read the whole chat under "## Translated chat" (`id [mm:ss] Japanese → Traditional Chinese`) and return **only the lines you change**, each as its id with the full corrected Traditional Chinese text. Return an empty list when nothing needs fixing.

Fix, in priority order:

1. **Consistency with the ground truth.** Every person, group, show, and term must match the "## Program briefing" (pre_pass.json), which is the reviewed final translation of the show. A surname, nickname, or kana spelling in chat maps to the same Traditional Chinese form.
2. **Consistency across the stream.** A recurring meme, catchphrase, nickname, or running joke must be translated the same way every time it appears. Pick the best rendering and align the others to it.
3. **Clear mistranslations**, such as a misread word or a reversed meaning, when the Japanese plainly says something else.
4. **Unnatural phrasing** that a Taiwanese viewer would not type in a chat room.

Do not touch a line that is already acceptable. Do not polish for style alone, lengthen lines, explain jokes, or add context the message does not have. Keep kaomoji, `w`/`ｗ` runs, symbols, numbers, and Latin-alphabet words as written.
