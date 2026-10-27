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
   `ocr.read_frames`. Boxes are fractions of the frame. It runs beside
   audio → refine, off the critical path.
2. **Reference** — glossary and proofread call `_telop.reference`: wait for
   the scan (`StageContext.side_tasks.wait`), build the cards, pass the
   offered ones as a table (`reference.render_reference`, prompt
   `postprocess/prompts/telop_reference.md`): only texts with kana or
   kanji (Latin-only reads were 28 % of rows, nearly all prints and
   garbage), a repeated text one row with all its times. Waiting keeps
   their input deterministic; no scan (failed task) → no table, a warning.
   Measured on 有吉 (2026-10-27): used as cited evidence by both passes, no
   sign it misled them.
3. **Select** — stage `telop` (15, after `chat_translate`, so no work dir
   was renumbered; `.work/15_telop/` holds `cards.json`, the session and
   `frames/`): waits for the scan, else scans itself; `scan.build_cards`
   (rewritten every run); the utility agent gets the offered cards as a TSV
   (seconds, coarse position, `echoes_speech`, lines joined ` / `), the
   dialogue as `seconds | ja | final zh` (`ja.srt` paired by index with
   `video.cht.srt`; the two must align, `check_aligned`, else the stage
   fails), the effective briefing and `instruction.telop`, plus
   `get_frames`. Answer = kept cards only (`{id, zh}`); validation: known
   id, once, non-empty `zh`. A translation that normalizes to its card's
   text (a kanji place name) is dropped by Python. Deliverable
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
   box is passed as `subtitles.ass.PictureBox`) and burns it **under the dialogue** (`BurnPlan.top`),
   which is always the top layer. `grill package --no-telop`
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
  it is copied in as its label (`熊元MEMO` over successive memos).
- **Grouping**: same timing (±1 s) and touching: stacked ≤ 0.5 of the
  smaller line height apart, on one row ≤ 0.25 of it apart (a space; two
  memos or name plates side by side sit further apart), or diagonal within
  the bigger line's height sideways unless both lines already have a line
  straight above/below (a memo's corner label joins its text; the label of
  the right one of two side-by-side memos does not join the left). Lines
  read row by row, left to right.
- **Kinds**: `speech` (every fresh line ≥ 60 % found in the ASR ±3 s) is
  offered marked `echoes_speech` — a card can echo the line and still add
  a label or a point; `candidate`; `recurring`.

## Placement (`render.py`)

Every translation sits beside its own card (`\pos`, pale yellow, size 42;
owner's call: one place for the eye, where the card is — a top line plus
some beside cards made the gaze drift): right below it (one side for every
item of a list), right above only when below does not fit in the picture;
anchored on the edge facing the card, so the estimated height never opens
a gap. Each spot is costed: share covered by another
translation (×10) or an original card (×1), distance from the preferred
spot (×3 per picture height, sideways too), being nearer another card than
its own (+1); spots tried are centred or against any obstacle's side
edges, times above/below or against any obstacle's top/bottom edges, kept
inside the picture. Widths are estimated (wide char = 1 em, other 0.55).
The dialogue is another layer on top: this one never avoids it (owner's
call: a translation away from its card is worse than one under a cue). A
card that groups wrongly lands wrongly, so fix grouping, not placement.

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
