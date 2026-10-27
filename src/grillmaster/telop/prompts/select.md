Pick the on-screen text cards (telops) of this Japanese variety show that Chinese viewers need translated, and translate them into Traditional Chinese (Taiwan).

The dialogue already has Chinese subtitles at the bottom of the screen. Your translations appear right beside each card as it shows (a long card's translation leaves once it has been read), so a viewer who cannot read Japanese still gets what the card says.

The cards below were read by OCR from frames sampled twice a second and pre-filtered by rules: OCR noise, cards without Japanese text and repeats of long-running fixtures are already gone. What is left still mixes real cards with scene text and OCR mistakes. Each card has an `id`, its on-screen time, a rough position (`vertical` when the card is set in vertical columns), `echoes_speech` (the numbers of the lines whose wording is mostly also said around that time) and its lines (numbered, top to bottom, ` / ` between lines). The dialogue table pairs each Japanese line with its final Chinese subtitle.

Keep a card when it adds something the dialogue subtitles do not carry:

- editorial commentary and ツッコミ captions, narration and questions put to the cast;
- information: places, dates, names with their group or age, profile tables, timelines, rules, prices that matter to the story, explanations of a word;
- thoughts, notes or messages the cast wrote, shown as text.

Drop a card when it:

- only repeats what someone is saying (that line already has a subtitle), even when the wording differs a little. `echoes_speech` marks the likely lines; keep a card whose lines are all marked when it carries more than the line does (a label saying whose card it is, a point the subtitle leaves out);
- is scene text: clothing prints, product labels, signage, posters, licence plates, set decoration, screens and monitors in the set — anything that belongs to the filmed place rather than added in editing;
- is the show's own logo, a corner or segment title, a broadcast-date or channel bug, a credit or a promotional banner;
- is a running readout: a timer, clock, counter or meter graphic and its label, whose numbers change while it is up (a card announcing a final result is not one);
- is unreadable OCR garbage, or a fragment of a card you already kept;
- would only repeat a name the subtitles already show;
- is in kanji alone (with numbers or symbols) and a Chinese viewer reads it right as written: a name, a place, a date, a count, a rank, a word that means the same in Chinese. Keep it when a word means something else in Chinese or is not used there, and when it is part of a card you keep that the rules split in two (shown at the same time right beside it, one plate or panel): then translate both parts.

When you cannot tell from the text whether a card is an edited caption or scene text, look at it: call `get_frames` with the card's start times (many cards in one call). Do not fetch frames for cards whose text already decides it.

Translation rules:

- Translate what the card means in context, in the tone of the show; captions are punchy, keep them short.
- Agree with the dialogue subtitles: use their renderings of names, terms and phrases the card shares with the speech, and the briefing's names (`characters` for talents). The subtitles are the reference for wording, not text to copy into a card.
- A name on a card covers the span the card writes ("Name span"; the card is the source).
- Keep the whole episode consistent: cards of one kind (the same recurring label, caption style or card format) get one rendering of their recurring parts and one layout, and the same Japanese wording gets the same translation wherever it appears. A label that says whose card it is belongs in the translation.
- Translate only the lines a viewer needs, and list them in `lines`; the translation sits beside those lines. The rules sometimes join two things into one card: a name plate or photo label beside a big caption. When the caption only repeats the dialogue, translate the plate or label alone and leave the caption's lines out; when both add something, keep them in one translation. The kanji rule above also applies line by line: leave out a line a Chinese viewer reads right as written that the rest of the card does not need, but keep one that says whose card or answer it is.
- One card is one entry. Use `\n` for a line break only where the card itself breaks into separate items (a name and a profile line); at most three lines. A `vertical` card's translation is set vertically beside it, each line a column, so keep it short.
- OCR may misread a character. Translate the card that is on screen, not the misreading; fetch a frame when it matters.
- No Japanese left in the translation except a proper noun with no Chinese rendering.
- English on a card (a replay mark, a slogan, a decorative word) is not translated: leave it out. A name in Latin letters is rendered as the subtitles render it.

Return only the cards you keep, each `id` once, with the numbers of the lines you translated in `lines` (all of them for a whole card) and the translation in `zh`. An empty list is a valid answer for a show whose cards all repeat the dialogue.
