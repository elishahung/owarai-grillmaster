Pick the on-screen text cards (telops) of this Japanese variety show that Chinese viewers need translated, and translate them into Traditional Chinese (Taiwan).

The dialogue already has Chinese subtitles at the bottom of the screen. Your translations appear as a separate line at the top of the screen while the card is visible, so a viewer who cannot read Japanese still gets what the card says.

The cards below were read by OCR from frames sampled twice a second and pre-filtered by rules: OCR noise, cards that repeat the speech, and repeats of long-running fixtures are already gone. What is left still mixes real cards with scene text and OCR mistakes. Each card has an `id`, its on-screen time, a rough position and its lines (top to bottom, ` / ` between lines).

Keep a card when it adds something the dialogue subtitles do not carry:

- editorial commentary and ツッコミ captions (`相変わらず何も知らない`, `唐突チェーン`), narration and questions put to the cast;
- information: places, dates, names with their group or age, profile tables, rankings, timelines, rules, prices that matter to the story, explanations of a word;
- written thoughts and notes such as memo cards; keep a label naming whose note it is (`有吉MEMO`) as a 【】 prefix.

Drop a card when it:

- repeats what someone is saying (that line already has a subtitle), even when the wording differs a little;
- is scene text: clothing prints, product labels, signage, posters, licence plates, set decoration — anything that belongs to the filmed place rather than added in editing;
- is the show's own logo, a corner or segment title, a broadcast-date or channel bug, a credit or a promotional banner;
- is unreadable OCR garbage, or a fragment of a card you already kept;
- would only repeat a name the subtitles already show.

When you cannot tell from the text whether a card is an edited caption or scene text, look at it: call `get_frames` with the card's start times (many cards in one call). Do not fetch frames for cards whose text already decides it.

Translation rules:

- Translate what the card means in context, in the tone of the show; captions are punchy, keep them short.
- Use the briefing's names and terms so the cards agree with the dialogue subtitles. Talent names follow the briefing's `characters`.
- One card is one entry. Use `\n` for a line break only where the card itself breaks into separate items (a name and a profile line); at most three lines.
- OCR may misread a character. Translate the card that is on screen, not the misreading; fetch a frame when it matters.
- No Japanese left in the translation except a proper noun with no Chinese rendering.

Return only the cards you keep, each `id` once, with its translation in `zh`. An empty list is a valid answer for a show whose cards all repeat the dialogue.
