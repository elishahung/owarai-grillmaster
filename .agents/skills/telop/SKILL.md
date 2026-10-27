---
name: telop
description: >-
  On-screen text cards (telops) under `src/grillmaster/telop/`: GPU OCR of
  sampled frames (`ocr.py`, optional `telop` extra), rule tracking and
  pre-filter into cards (`scan.py`), the utility agent that keeps and
  translates them (`select.py`, `prompts/select.md`) and the top-centred ASS
  layer packaging burns in (`render.py`); glue `stages/telop.py` (side task
  and `grill telop`). Read this before changing the OCR engine, the
  pre-filter thresholds, the selector, the telop layer or how they reach the
  package.
---

# Telops

Japanese variety shows put much of their content in edited on-screen cards
(commentary/ツッコミ captions, memo cards, name and profile cards, segment
titles) that ASR never hears. This subsystem finds them, lets an agent pick
the ones the dialogue subtitles do not carry, and burns the translations in
at the top of the package.

## Flow and artifacts

Side task `telop` (`start_after = prepass`: it needs `ja.srt` and the
briefing; it runs beside chunks → proofread, off the critical path).
Enabled by `--telop`, `[features] telop`, or a program's `telop = true`;
`SideTaskDef.applies` checks the program at the start point, since
`enabled` sees no state (it stays on while any program sets `telop`).
`grill telop <dir|id>` runs the same task on a finished or archived project
(`pipeline.side_tasks.as_delivery_step`, alone in `registry.TELOP_ONLY`;
recorded in `state.side_tasks.telop` like the side task), then `grill
package` burns it.

1. **Scan** (`.work/side/telop/scan.json`, fixed-name cache): ffmpeg samples
   the video at `SAMPLE_FPS` (2) at exactly 1920x1080 into a local temp dir
   (`media.frames.sample_frames`; a gigabyte of stills, and the project may
   be on the NAS), then `ocr.read_frames` OCRs each one. Boxes are stored as
   fractions of the frame.
2. **Pre-filter** (`scan.build_cards` → `cards.json`, rewritten every run,
   deterministic): track boxes into lines (IoU + reading similarity, a 2-frame
   gap allowed, most-trusted reading wins; a different reading in the same
   box starts a new line, so the next memo in the same frame is its own
   card) → drop noise (< 1 s, one char, confidence < 0.7) → mark lines
   recurring (> 60 s episode-wide; a text counts the time of near-identical
   reads and of longer texts it is a fragment of, never of its own
   fragments, or a memo quoting a recurring phrase would vanish) and
   speech-like (≥ 60 % of chars in the ASR ±3 s) → union lines sharing
   timing (±1 s) **and** touching on screen into cards → kind: `recurring`
   (all lines recurring), `speech` (all fresh lines speech-like), else
   `candidate`. Recurrence is judged per line but applied per card, so a
   recurring "○○MEMO" label stays attached to its fresh memo.
3. **Select** (role `utility`, `SchemaOutput(TelopSelection)`, `get_frames`
   into `.work/side/telop/frames/`): candidates only as a TSV (seconds,
   coarse position, lines joined ` / `), the plain ASR transcript, the
   effective briefing, `instruction.telop`. Answer = kept cards only
   (`{id, zh}`); validation: known id, once, non-empty `zh`.
4. **Deliverable** `.subs/telop.cht.json` (`TelopSubtitles`: id, start, end,
   ja, zh, box). Hand-editable; an existing file is a hit (no agent). Delete
   it to select again from the kept scan; delete `scan.json` to re-OCR.
5. **Package**: `package.assemble.with_telops` renders it to
   `.work/package/telop.ass` as the top layer over `build_burn_plan`'s
   dialogue and chat; over a shrunk `side`-layout picture it gets
   `telop.render.telop_force_style` margins. `grill package --no-telop`
   (`RunOptions.burn_telops`, package only) leaves the layer out. Not part of
   `video.cht.ass` (finalize ran long before the side task joins).

## OCR engine

PP-OCRv5 server detection + PP-OCRv6 medium Japanese recognition through
RapidOCR on onnxruntime-gpu (CUDA EP, `cudnn_conv_algo_search=HEURISTIC`,
`arena_extend_strategy=kSameAsRequested`). Missing extra or CUDA provider →
`TelopError` (side task fails loudly, no CPU fallback). Install with
`uv sync --extra telop`; `[tool.uv] required-environments` pins the lock to
Windows wheels (the newest CUDA runtime wheels are Linux-only). A plain
`uv sync` removes the extra again.

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
time). Measured end to end on two 24-min episodes read from the NAS: ~10 min
scan (sampling, then OCR; streaming the sampling into the OCR would hide
1–2 min) + ~5 min selection.

## Selection policy and tuning

- The chunk translator never selects or sees telops: the weaker model only
  translates. Selection belongs to `utility`.
- Recurring cards (corner/segment titles) are not offered: shown at the
  top they stayed up far too long (owner's call, 2026-10-27).
- Rules only shrink the selector's input; they never decide what a viewer
  sees. Scene text (shirt prints, labels, signage) survives the rules on
  location shows; the selector drops it, with frames when the text is
  ambiguous. A rule based on the look of one show's cards was rejected
  (cards differ per show).
- Telops do not feed refine/glossary/proofread (they run on the critical
  path; the side task joins later). Revisit only with a measured benefit.
- Thresholds in `scan.py` are tuned constants, not settings.
