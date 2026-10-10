---
name: live-chat
description: >-
  Opt-in YouTube live-chat replay (`--chat`) under `src/grillmaster/live_chat/`:
  normalize (`parse.py`; download in `sources/live_chat.py`), batch translation
  + whole-stream polish (`translate.py`, `prompts/`), and the scrolling
  chat-panel ASS renderer (`render.py`, `layout.py` side/overlay) burned in at
  package time; stages `stages/chat_fetch.py` and `stages/chat_translate.py`.
  Read this before changing chat stages, chat caches, the chat prompts, the
  panel layout, or how packaging layers the chat panel.
---

# Live-chat replay

Both stages use `enabled=chat_enabled` (`RunOptions.chat`, which `--chat` sets on `grill run` and
`grill serial`). Without it they are skipped as DISABLED. `--chat` lasts one run only:
neither `grill.toml` nor `project.json` stores it, so a resume must pass it again.
For registry and ledger rules, see **project-architecture**.

| Stage key | Placement | Writes (`project/layout.py`) |
|---|---|---|
| `chat_fetch` | after `combine`, before `audio` | `work/04_chat_fetch/live_chat.jsonl` (raw yt-dlp track, cache), `messages.json` (`ChatLog`, re-parsed each stage run) |
| `chat_translate` | last stage, after `finalize` | `work/13_chat_translate/batches/batch_NNNN.json`, `polish.json`, `session_<label>/`; output `subs/chat.cht.json` (`TranslatedChatLog`) |

- Fetch runs early so a video without a replay raises `SourceError` before ASR spends money.
  Times come from `videoOffsetTimeMsec`, not from ASR. `parse_live_chat` drops messages outside
  `ProjectState.section` and rebases the rest onto the cut `video.mp4`.
- Normalization keeps text messages and Super Chats (`kind="paid"`, `amount`). It drops
  membership, gift and system items, and emoji runs, which libass cannot draw.
  `ChatMessage.id` is the message's final position in the log.
- Translate runs after finalize, so `video.cht.srt` and `effective_briefing()` serve as ground
  truth. `subs/ja.srt` is paired with `video.cht.srt` by position, only when the block counts match.
  A chat failure leaves the main subtitles finalized.
- `live_chat` must not import `grillmaster.project` or `grillmaster.config` (import-linter). The
  stage passes in `ChatTranslationInputs` and `ChatTranslationFiles` (cache paths, session dirs).

## Translation (`translate.py`)

- Only messages containing kana or kanji go to an agent (`needs_translation`). `plan_batches` sets
  N = ceil(count / `BATCH_SIZE`) and keeps batch sizes within 1 of each other. Boundaries depend
  only on the message list, and caches are keyed by batch number, so changing the split rule
  means deleting `batches/`.
- Each batch prompt contains the compact briefing JSON, the subtitle pairs from
  `SUBTITLE_LEAD_SECONDS` before the batch to `SUBTITLE_TAIL_SECONDS` after it, and the previous
  `EARLIER_CHAT_COUNT` messages as read-only context.
- Batches run through `AgentRunner.run_jobs`. Each job's `accept` writes its batch cache
  immediately. All pending batches finish before `ChatTranslateError` is raised, so a resume
  re-runs only the failed ones. Then a single polish call (`agents.run`) returns only the lines
  it changed. See **agent-orchestration** for the runner and the repair loop.
- Schemas: `ChatBatchTranslation{translations:[{id,text}]}` and
  `ChatPolish{corrections:[{id,text}]}`. `validate` uses `core/id_coverage.py`, which chunk
  translation also uses. A batch must cover every id. Polish may return any subset of the
  translated ids. Unknown, duplicate or empty ids raise `ValidationFailure`.
- Chat has no refine or glossary pass. Both prompts append `prompts/name_form.md`, the glossary
  check's span, honorific and subject parity rules; keep it in step with that prompt.
- The role is `Role.CHAT`, i.e. `[agents.roles] chat`. If omitted it falls back to `utility`
  (`AgentRoles.spec`). Concurrency uses the runner's global slots.
- Caches use fixed filenames and never invalidate themselves; a parseable file skips the agent.
  To re-run a step, delete a batch file or `polish.json`, or run
  `grill reset <id> --only chat_translate`.

## Rendering and packaging

- The chat panel is not a stage. `package/assemble.py::build_burn_plan` renders
  `work/package/chat.ass` from `subs/chat.cht.json` on every package, so a layout change only
  needs `grill package`. The dialogue alone burns on the full frame when the chat file is
  missing, when the layout is `none`, or when rendering fails (warned).
- `--chat-layout` (default `side`) is read only at package time, on `run`, `serial` and
  `package`. `render._LAYOUTS` maps each layout to a `_Panel` and a `ChatPlacement`
  (`picture: PictureBox | None` plus the dialogue `force_style`):
  - `side`: the 16:9 picture is shrunk into the left `SIDE_VIDEO_WIDTH` box. Chat fills the
    black right column with no background and scrolls out at the top edge. The dialogue moves to
    the bottom bar via `force_style` margins built from `subtitles.ass.MARGIN_H`.
    `video.cht.ass` is never rewritten.
  - `overlay`: full frame, with a translucent rounded panel on the right that ends above the
    dialogue.
- `package/render.py::BurnPlan` owns the ffmpeg syntax. The layers are `(chat.ass, dialogue)`,
  so the dialogue draws on top. The picture box becomes `scale`+`pad` after the look filters
  and before the `subtitles=` layers, in one `-vf` graph. The box must stay inside the frame
  and use even values. Layers stay on the source timeline, so tempo, trim and remix apply
  unchanged. Burn-in itself is covered in **postprocess-and-packaging**.
- ASS scroll is computed per *state*: between two arrivals, each visible message gets its own
  events (`\move` slide-up, `\fad` for the newcomer), clipped to the panel edge. Events grow as
  messages × visible rows (~200k for a 6k-message stream), so keep per-message events few and
  short and put fixed looks in `Chat*` styles.
- Canvas, font and margins come from `subtitles/ass.py`. `*_LINE_HEIGHT` and `*_WIDTH_ADVANCE`
  in `render.py` are libass measurements for that font, so re-measure them if the font or sizes
  change.
