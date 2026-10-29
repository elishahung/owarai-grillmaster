---
name: telop
description: >-
  On-screen text cards (telops) under `src/grillmaster/telop/`: GPU OCR of
  sampled frames (`ocr.py`, optional `telop` extra), rule tracking and
  grouping into cards (`scan.py`), the utility agent that keeps and
  translates them (`select.py`, `prompts/select.md`), the reference table
  glossary/proofread read (`reference.py`) and the ASS layer packaging burns
  in beside each card (`render.py`); glue `stages/telop_scan.py`
  (side task), `stages/telop.py` (last stage, `grill telop`) and
  `stages/_telop.py`. Read this before changing the OCR engine, the
  grouping thresholds, the selector, the placement or how telops reach the
  other stages and the package.
---

# Telops

Japanese variety shows put much of their content in edited on-screen cards
(commentary/ツッコミ captions, memo cards, name and profile cards, maps)
that ASR never hears. This subsystem reads them, gives them to the
glossary check and proofread as evidence, lets an agent pick and translate
the ones the dialogue subtitles do not carry, and burns those in.

## Flow and artifacts

On for a project with `--telop`, `[features] telop`, or its program's
`telop = true` (`stages._telop.enabled` sees no project and stays on while
any program may ask; `applies` decides per project, for the side task and
the stage alike).

1. **Scan** — side task `telop_scan`, after `combine`
   (`.work/side/telop_scan/scan.json`, fixed-name cache; delete it to
   re-OCR): ffmpeg samples the video at `SAMPLE_FPS` (2) at exactly
   1920x1080 into a local temp dir (`media.frames.sample_frames`; a
   gigabyte of stills, and the project may be on the NAS), then
   `ocr.read_frames`. Boxes are fractions of the frame. Frame `i` shows
   the video just before `(i + 0.5) / fps` (the fps filter; measured), so a
   card's times are its first and last frames' moments widened by
   `CARD_EDGE_S` (0.1 s) each side (the bare moments cut it short on
   screen; owner's call). It runs beside
   audio → refine, off the critical path.
2. **Reference** — glossary and proofread call `_telop.reference`: wait for
   the scan (`StageContext.side_tasks.wait`), build the cards, pass the
   offered ones as a table (`reference.render_reference`, prompt
   `postprocess/prompts/telop_reference.md`), a repeated text one row
   with all its times. Waiting keeps their input deterministic; no scan
   (failed task) → no table, a warning.
   Measured on 有吉 (2026-10-27): used as cited evidence by both passes, no
   sign it misled them.
3. **Select** — stage `telop` (15, after `chat_translate`, so no work dir
   was renumbered; `.work/15_telop/` holds `cards.json`, the session and
   `frames/`): waits for the scan, else scans itself; `scan.build_cards`
   (rewritten every run); the utility agent gets the offered cards as a TSV
   (seconds, coarse position + `vertical`, `echoes_speech` = numbers of the lines that
   repeat the speech, lines numbered `[n]` and joined ` / `), the
   dialogue as `seconds | ja | final zh` (`ja.srt` paired by index with
   `video.cht.srt`; the two must align, `check_aligned`, else the stage
   fails), the effective briefing and `instruction.telop`, plus
   `get_frames`. Answer = kept cards only (`{id, lines, zh}`); validation:
   known id, once, non-empty `zh`, `lines` non-empty, unique, on the card.
   The item's `ja` and `boxes` (one per line) are those lines', its
   `beside` the boxes of the card's other lines (avoided, not anchored
   on), so the translation sits beside them: a name plate grouping joined to a caption that repeats the
   speech is translated alone, beside the plate (grouping cannot split
   them, see the strict xfails in `test_scan.py`). A translation that
   normalizes to its lines' text (a kanji place name) is dropped by Python. Deliverable
   `.subs/telop.cht.json` (`TelopSubtitles`, the stage's declared output), hand-editable;
   `grill reset <id> --only telop` selects again. The stage also writes
   `video.cht.telop.ass` at the root (`render.render_with_dialogue`: the
   `video.cht.srt` dialogue plus the full-picture telop layer, telops first
   so the dialogue draws on top) for external playback; a hand edit of the
   JSON does not refresh it.
4. **`grill telop <dir|id>`** runs the stage on a finished or archived
   project (`pipeline.delivery.as_delivery_step`, alone in
   `registry.TELOP_ONLY`) whatever the ledger says — archives cannot be
   reset — and records it in the ledger; it scans first when no scan is kept.
5. **Package** — `package.assemble.with_telops` renders
   `.work/package/telop.ass` inside the picture (a `side` layout's shrunk
   box is passed as `subtitles.ass.PictureBox`), placed against
   `video.cht.srt` where the layout sets it (`BurnPlan.dialogue_margins`), and
   burns it **under the dialogue** (`BurnPlan.top`), which is always the top layer. `grill package --no-telop`
   (`RunOptions.burn_telops`) leaves it out.

## Cards (`scan.py`)

Rules only shape the selector's input; the agent decides what a viewer
sees. Thresholds are tuned constants (on a memo show and a caption-heavy
location show), not settings.

- **Tracking**: a box continues a line at (overlap, reading match) ≥ (0.5,
  0.75) or (0.3, 0.9); the match is over the shorter reading, so a fade-in
  fragment matches fully, but a new name in a label's place (熊元MEMO →
  小橋MEMO) starts a new line. Noise: < 1 s, one char, confidence < 0.7.
- **Recurring**: > 60 s on screen episode-wide (asymmetric: a text counts
  near-identical reads and longer texts it is a fragment of). Recurring
  lines never join fresh lines into a card (a logo under a row of name
  plates chained them); they form their own `recurring` cards, never
  offered, and a recurring line set flush on a card (≤ half the smaller
  line height above/below) that stays up across it and starts or ends with
  it is copied in as its label (`熊元MEMO` over successive memos). Two
  fresh groups on screen together on either side of one such label are
  one card (a plate whose middle column is also the corner title); boards
  bridged this way are left to the selector. Only an edited-in text labels
  or bridges: one whose 3 steadiest places hold ≥ half its time or whose
  character size varies ≤ 0.2 (`_Recurs.OVERLAY`). Scene text moves and
  rescales with the shot (a shirt print by a name plate, bottles, menus,
  product copy, measured 2026-10-29); a memo label in two rows or a
  recommender's label on plates anywhere stays a label (cases in
  `test_scan.py`).
- **Grouping**: same timing (±1 s) and touching: stacked ≤ 0.5 of the
  smaller line height apart, on one row ≤ 0.25 of it apart (a space; two
  memos or name plates side by side sit further apart), or diagonal within
  the bigger line's height sideways unless both lines already have a line
  straight above/below (a memo's corner label joins its text; the label of
  the right one of two side-by-side memos does not join the left). Lines
  above each other whose character sizes differ > 3.5× stay apart (name
  plate over a caption, footnote under a title, logo by a caption; owner's
  call, 2026-10-28); a smaller lead-in over a big caption stays under the
  ratio and joins it. So do lines set the same way that read as one row (a
  big caption's box reaching up into a small tagline's row; signs, posters,
  ages beside a caption); a lead-in label over 3.5× smaller now splits off
  too (accepted, 2026-10-28). Recurring labels pass the same check.
  Lines read row by row, left to right.
- **Orientation**: a line ≥ 1.2× taller than wide (pixels) is vertical; a
  card is vertical when its biggest-character line is, and reads in columns
  right to left. Two vertical lines are measured transposed: columns side
  by side are stacked rows, gaps in character width (a vertical group name
  labels its name plate). A vertical and a horizontal line (fresh or a recurring
  label) join only as label and text: the label ≤ 6 characters and ≤ 0.75
  of the other's character size (このあと, 私の好きは, 理由 stay; a speech
  bubble beside a vertical caption, a corner title over one split; owner's
  calls 2026-10-28, cases in `test_scan.py`).
- **Kinds**: `speech` (every fresh line ≥ 60 % found in the ASR ±3 s;
  each such line is marked `CardLine.echoes_speech`, also on other cards)
  — a card can echo the line and still add a label or a point;
  `candidate`; `recurring`.
- **Offered** (`TelopCards.offered`, per card after grouping): also needs
  kana or kanji; the rest (~25–35 % on six episodes) are timer digits,
  prints, English marks, garbage. `cards.json` keeps them.
- **Kanji-only cards** are offered; the selector drops those a Chinese
  viewer reads right as written (owner's call, 2026-10-27). A Python rule
  was measured and rejected: it also dropped Japanese-only terms (対抗,
  芸歴) and a profile plate's kanji half when grouping split the plate.

## Placement (`render.py`)

Every translation sits beside its own card (`\pos`, pale yellow, size 42;
owner's call: one place for the eye, where the card is — a top line plus
some beside cards made the gaze drift): right below it (one side for every
item of a list), right above only when below does not fit in the picture;
anchored on the edge facing the card, so the estimated height never opens
a gap. Above/below is measured per line, on the translated lines sharing
its columns (a tab or corner label off to one side made the union box
push it away, 2026-10-28). Card lines count by the text's own extent
(`ocr.text_box`): the detector's box reaches past big text by up to 80 px,
which left a visible gap. Each spot is costed: share
of the smaller of the two covered by another translation (×10), a
translated line of any up card, its own or another's (×10: one hidden
leaves its translation nothing to be read against; at ×1 a translation
squeezed onto its own plate's group name, 2026-10-28), or a `beside` line
(×1; a short label half
hidden counts as half), distance from the preferred
spot (×3 per picture height, sideways too), being nearer another card than
its own (+1); spots tried are centred or against any obstacle's side
edges, times above/below or against any obstacle's top/bottom edges, kept
inside the picture. Widths are estimated (wide char = 1 em, other 0.55).
A translation leaves with its card, or `MAX_SHOWN_S` after it appears when
the card is up longer than that plus `SHOWN_TOLERANCE_S` (owner's call:
long cards stayed up long after they were read); its card
stays an obstacle until the card itself leaves. `telop.cht.json` keeps the
card's real times.
A vertical card's translation is set vertically (owner's call, 2026-10-28;
the card's way from `boxes` + `beside`, as the selector's table marks it):
libass's own vertical mode (style `TelopVertical`: font `@<font>`, angle
270; one event per column), right of the card or left when right does not fit, centred on it
top to bottom. That mode turns Latin runs but keeps horizontal glyphs for
brackets, ellipses, dashes and waves beside CJK, so `render._VERTICAL_FORMS`
swaps in the vertical presentation forms (measured; commas and stops stay).
Lines split into evenly filled columns, and 2-3× shorter column layouts are
tried at `_MORE_COLUMNS` per extra column, so it fits beside a card with
another card above or below it.
The dialogue draws on top (2026-10-29). It only chooses between the spot
the costs above pick and spots right by the card (within `_GAP`: the other
side, along an edge), never one away from it: weighed against distance it
pulled translations 70-500 px off their cards and over other cards' ones
(owner: unacceptable; tests lock both). Among those, each cue's rows are
estimated as libass draws them (`subtitles.ass.dialogue_rows`: rows
60 px apart, outline 8, measured), a translation line counts hidden for the part of its length a
row covers at least half across (a touch costs nothing), and the share of
lines and time hidden counts squared (`_COVER_DIALOGUE`). A card that
groups wrongly lands wrongly, so fix grouping, not placement.
Known gap: an untranslated card is no obstacle (render sees only
`telop.cht.json`), so a translation can land on a big caption the
selector left out. Counting them (2026-10-28, 560 moments) moved 26-69
depending on weight and which kinds count, as many worse (lists broken,
pushed into the dialogue) as better; not adopted. Also a gap: a card's
lines count for its whole span, though one may appear up to
`CARD_TIMING_S` later (`telop.cht.json` keeps card times only), so a
translation can make room for a line not yet up.

## OCR engine

PP-OCRv5 server detection + PP-OCRv6 medium Japanese recognition through
RapidOCR on onnxruntime-gpu (CUDA EP, `cudnn_conv_algo_search=HEURISTIC`,
`arena_extend_strategy=kSameAsRequested`). Missing extra or CUDA provider →
`TelopError` (no CPU fallback; the side task logs it and the stage fails
on its own retry). The stage's `preflight` checks the extra is installed
whenever telops are enabled, so a missing one fails before any stage
rather than after the whole translation. Install with `uv sync --extra telop`; `[tool.uv]
required-environments` pins the lock to Windows wheels (the newest CUDA
runtime wheels are Linux-only). A plain `uv sync` removes the extra again.

Chosen in a 2026-10 benchmark (44 hand-labelled frames, RTX 5090; gate:
whole-episode OCR ≤ half the runtime, i.e. ≥ 4 frames/s at 2 fps):

| engine | content CER | missed | fps | note |
|---|---|---|---|---|
| v5 server det + v6 medium ja rec | 1.5 % | 0 | 6.2 | chosen; stable boxes (σ 1–3 px) |
| v5 server det+rec | 4.0 % | 0 | 6.8 | Chinese glyph drift (歷, 宽) |
| v5 mobile | 7.2 % | 5 | 10.5 | boxes jump up to 167 px; breaks tracking |
| v6 medium (det+rec) | 6.2 % | 8 | 8.3 | drops small coloured chips |
| YomiToku | 6.7 % | 2 | 7.8 | 76 garbage lines; CC BY-NC-SA |
| v5 det + PaddleOCR-VL-1.6 rec | 1.9 % | 0 | 1.8 | best overall, too slow per frame |
| Surya / EasyOCR / manga-ocr | ≥ 13 % | | | rejected |

Whole-frame OCR-VLMs (PaddleOCR-VL, HunyuanOCR, DeepSeek-OCR-2) run at
0.3–0.8 fps; general VLMs (Qwen-VL) are out by the owner's choice (VRAM,
time). Measured on two 24-min episodes read from the NAS: ~10 min scan
(beside the translation) + ~5 min selection (now on the critical path,
accepted for selecting beside the final subtitles).

## Policy

- The selector joins the shared `name_span_rules()` (translate-pipeline):
  a name on a card keeps the span the card writes.
- The chunk translator never selects or sees telops: the weaker model only
  translates. Selection belongs to `utility`, after finalize, so card
  wording follows the final subtitles and briefing.
- Recurring cards are never offered: translated they stayed up far
  too long (owner's call, 2026-10-27).
- `prompts/select.md` and `telop_reference.md` stay show-agnostic: no one
  show's card conventions and no examples quoted from the test episodes
  (they inflate those tests and hide consistency gaps). A show's convention
  goes in its `instruction.telop`.
- Do not force what OCR cannot read (a card missing from the scan stays
  missing); guide the rules and the agent instead.

## Acting on feedback

The owner reviews episodes and reports cards by timestamp. Use
`scripts/telop_probe.py` (run it from the Bash tool, which shows its UTF-8
output as is; its docstring lists the commands) rather than ad-hoc scripts.
It rebuilds cards with the working tree's rules from the kept scan, so no
OCR or agent run is needed. Per item:

1. Trace it down the chain before changing anything: `cards <id> <m:ss>`
   (grouping, kind, offered, the last translation), `lines <id> <regex>`
   (raw OCR lines with pixel boxes; match the raw text, since `normalize`
   drops ー・… and punctuation), `frames <id> <m:ss>...` to look. Name the
   stage that went wrong.
2. Fix it at the layer that owns it (below), as a class of card, never
   this episode's text.
3. Measure a rule change on every scanned project: `diff <HEAD's scan.py>`
   (`git show HEAD:src/grillmaster/telop/scan.py > "$TEMP/old_scan.py"`;
   ~30 s over the archive). Read what it lets in or drops elsewhere. A
   rule that fixes the episode but admits logos, sponsor marks, ad copy or
   board entries elsewhere is rejected. OCR boxes include decoration and
   vary for the same text (one corner title 46 px vs 77 px tall), so size
   thresholds are weak signals. Diff entries you cannot call improvement
   or regression go to the owner as images from `review <HEAD's scan.py>`
   (boxed before/after stills), sent straight into the conversation, each
   with a Chinese line on what the card is and your verdict. The owner
   does not read Japanese: never report cards by their text alone, and
   never as a page to open.
4. Lock each settled case into `tests/telop/test_scan.py` with its real
   boxes (`lines` prints them to ~1 px): the reported card, and any card a
   rejected rule broke elsewhere. A gap with no fix yet is a strict
   `xfail`. The suite accumulates, like `tests/asr/test_srt_builder.py`;
   a new rule must pass every case.
   A placement change is measured the same way with `placement <HEAD's
   render.py>`: the kept translations of every selection placed by both,
   burned in with the dialogue, before/after per moment. Show those, not
   boxes, whenever the question is where a translation lands. Both
   commands read every project; one they cannot read is migrated to the
   current format, never skipped.
5. When no general fix exists, report the cause and the options instead
   of forcing one. Mind intro cards: name, group, age and profile lines
   group into one card; a rule on single lines (length, digits) splits or
   strips them.

Where a fix belongs:

- **Pre-filter rules** (`scan.py`, deterministic, before any agent): only
  what one line's or card's text, box and timing decide and is noise in
  every show: confidence, duration, one character, no kana/kanji,
  long-running fixtures; speech echo is marked, not dropped. The bar: the
  cross-episode diff loses no real card.
- **Placement** (`render.py`, deterministic, after selection): where a
  correctly grouped and selected card's translation lands: the costs and
  spots above. Lock cases in `tests/telop/test_render.py` with real boxes.
- **Selector agent** (`select.md`, show-agnostic): anything needing
  meaning or context: edited caption vs scene text, logos/titles/ads,
  running readouts, echoes of speech, what a viewer needs, wording and
  consistency, English parts. A show's own convention goes in its
  `instruction.telop`.
- **New step or algorithm** (propose with measurements, owner decides):
  when the signal is across lines or time rather than in one line, so
  neither a single-line rule nor the prompt can see it: same text in
  another place or form (below), a box whose text keeps changing (a
  readout), boards. Expect these to need a richer model of the scan
  (positions, layouts, sequences), not another threshold.

Known gap (2026-10-27): whether a text recurs is judged by text alone,
wherever and however it appears (only labelling looks at places and sizes,
above). A corner label's text reused elsewhere is absorbed: a
full-screen title card or break bumper repeating the corner title, an
intro name plate whose name is also a long-running label. Size/orientation
"set apart" exceptions were measured and rejected (on other episodes they
mostly admitted logos, sponsor marks and boards). Recurrence counted only
at a line's own place was measured and rejected for labelling (2026-10-29):
memo labels alternate between rows, and a plate's group name recurs only
as the corner title. Position-aware recurrence is still the open lead for
the absorbed cards (unmeasured): `_Recurs` already finds each text's
steadiest places.

Scene text seen once (a poster, a whiteboard) still reaches the selector,
which drops it (the reference tables keep it on purpose: it tells glossary
and proofread what is on screen). Filtering it by box motion was measured and
rejected (2026-10-29, 22 selections): a card whose box keeps moving between
same-reading frames is ~5 % of those offered and kept 1 % of the time (19 %
when still), but zooming, bouncing or shaking overlays move too, some
translated; tripod shots and short cards are not caught.
