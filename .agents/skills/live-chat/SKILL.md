---
name: live-chat
description: >-
  Opt-in YouTube live-chat replay (`--chat`) under `services/live_chat/`:
  normalize (`parse.py`, download in `services/ytdlp/live_chat.py`), batch
  translation + whole-stream polish (`translate.py`, `prompts/`), and the
  scrolling chat-panel ASS renderer (`render.py`, side/overlay layouts) burned in at package time. Read this before changing chat
  stages, chat caches, the chat prompts, the panel layout, or how packaging
  layers subtitles.
---

# Live-chat replay

Projects run without `--chat` never touch this package; its two stages are
`run_optional` and skip as disabled. `--chat` is per-run only (no `.env`
toggle), like `--refine`: a resume must pass it again.

## Flow and placement

| Stage | Placement | Writes |
|---|---|---|
| `CHAT_FETCHED` | right after `VIDEO_PROCESSED` | `.live_chat/live_chat.json` (raw yt-dlp `live_chat` track, JSON lines), `.live_chat/messages.json` (`ChatLog`) |
| `CHAT_TRANSLATED` | last stage, after `FINALIZED` | `.live_chat/batches/batch_NNNN.json`, `.live_chat/polish.json`, `chat.cht.json` (`TranslatedChatLog`) |

- Fetch sits early so a video with no replay fails before ASR spends money.
  Message times come from `videoOffsetTimeMsec`; no ASR.
- Section runs: `process_video` persists `Project.section_start/section_end`;
  `parse_live_chat` drops messages outside the section and rebases the rest
  onto the cut `video.mp4` timeline.
- Translate sits after finalize on purpose: the finalized SRT (paired with
  `video.ja.srt` by index when block counts match) and the glossary-checked
  `pre_pass.json` are the ground truth for names and context, and a chat
  failure leaves the main subtitles finalized (archive/package wait for the
  resume).
- Normalization keeps text messages and Super Chats (`kind="paid"`,
  `amount`); drops membership/gift/system items and emoji runs (libass draws
  no colour glyphs or channel emoji images).

## Translation

- Only messages with kana/kanji go to the model (`needs_translation`); `www`,
  kaomoji, Latin pass through. Like the subtitle chunker, `plan_batches`
  takes N = ceil(count / `BATCH_SIZE`) and balances sizes (differ by ≤1).
  Boundaries depend only on the message list; batch caches are keyed by
  index, so changing the split rule requires deleting `.live_chat/batches/`.
- Each batch prompt: pre-pass JSON + subtitle pairs from
  `SUBTITLE_LEAD_SECONDS` before the first message (chat lags the stream) +
  `EARLIER_CHAT_COUNT` read-only prior messages. `validate` enforces exactly
  one non-empty translation per input id.
- Then one whole-stream polish call returns **only changed lines** (cross-batch
  consistency of names/memes, clear mistranslations). There is no
  refine/glossary pass for chat; instead both prompts append
  `prompts/name_form.md`, the glossary check's span/honorific/subject parity
  rules restated for chat — keep it in step with `glossary_check.md`.
- Model: `settings.chat_model` = `AGENT_CHAT_MODEL`, else `AGENT_COMMON_MODEL`.
  Concurrency comes from `settings.agent_concurrency` (shared with chunk
  translation).
- Caches are fixed-filename, never self-invalidate; delete a batch file or
  `polish.json` to re-run that step. A failed batch fails the stage after the
  others finish.

## Rendering and packaging

- `chat_burn_plan` is the one place that picks the package `BurnPlan`. It
  renders `video.chat.ass` from `chat.cht.json` at package time (not a stage —
  layout changes only need `grill package`). No chat, a failed render
  (warned), or layout `none` → dialogue only on the full frame.
- `--chat-layout` (`grill`/`serial`/`package`, package-time only; default
  `DEFAULT_CHAT_LAYOUT`; `grill package --skip-chat` = `none`). Each layout
  is one `_LAYOUTS` entry (panel, picture box, dialogue `force_style`):
  - `side` (default): the graded 16:9 picture is letterboxed into the left
    `SIDE_VIDEO_WIDTH` box; chat fills the black right column edge to edge
    (no panel background — per-frame shape blending was a measurable cost;
    messages scroll out at the frame's top edge); the
    dialogue keeps its size in the bottom bar via `force_style` margins
    derived from finalize's `ASS_MARGIN_H` (`video.cht.ass` is never
    rewritten).
  - `overlay`: full frame, translucent rounded panel over the right side.
- `BurnPlan` (media layer) owns the ffmpeg syntax: a typed `picture` box
  becomes `scale` + `pad` to the package frame (validated to stay inside it,
  so content still concats with full-frame noise), after the CPU look filters
  and before the `subtitles=` layers, in the same single `-vf` graph — no
  extra process, graph, or encoder change. Layers stay on the source
  timeline, so tempo/trim/parts apply unchanged.
- ASS cannot move one event through several positions, so the scroll is per
  *state*: between two arrivals every visible message gets its own events
  (`\move` slide-up, newcomer `\fad`), clipped to the panel's outer edge.
  Events ≈ messages × visible rows × 3 (a ~100 min, 6k-message stream is
  ~225k events / 33 MB). Measured cost is drawing the visible rows (~2 ms per
  frame), not scanning events. Per-message strings are built once in
  `_Item`; fixed looks live in the `Chat*` styles, not per-event tags.
- Layout constants live at the top of `render.py`; canvas, font, and
  dialogue margins come from `services/finalize` (`ASS_PLAY_RES_*`,
  `ASS_FONT_NAME`, `ASS_MARGIN_*`), and the overlay panel ends above the
  bottom-centered dialogue. Line pitches and glyph advances
  (`*_LINE_HEIGHT`, `*_WIDTH_ADVANCE`) there are measured libass values for
  that font; stacking and wrapping use them, so re-measure if the font or
  sizes change. The Super Chat amount differs by colour/weight only — a
  larger size would stretch the name line.
