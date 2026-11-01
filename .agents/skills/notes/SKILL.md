---
name: notes
description: >-
  Translator's notes (譯者註) under `src/grillmaster/notes/`: the editor agent
  that decides what a Taiwanese viewer is missing (an event, a person, a work,
  a custom the talk leans on) and writes short notes (`annotate.py`,
  `prompts/`), the dry-run probe used to tune that decision, and the ASS layer
  drawn at the top of the picture (`render.py`); glue `stages/notes.py` (last
  stage, `grill notes [--probe]`). Read this before changing when a note is
  written, the prompts, the timing, the placement or how notes reach
  `video.cht.ass` and the package.
---

# Translator's notes

## Flow and artifacts

On for a project like telops (`_common.switch_enabled/switch_applies`):
`--notes` / `--no-notes`, then the program's tri-state `notes` (series over
channel), then `[features] notes`. Role `editor` (shared with the proofread;
omitted = postprocess), `WEB_SEARCH` required.

1. **Stage `notes`** (16, the last; `.work/16_notes/` holds the session and
   `frames/`): reads `video.cht.srt` beside `ja.srt` (must align; the plain
   transcript, so archives from before voice tags work too),
   `.subs/telop.cht.json` when present (what the viewer already reads),
   the kept telop scan's cards (`_telop.kept_cards`: the offered ones as
   the on-screen text table, evidence only; no OCR runs for notes), the
   broadcast date (date research's verdict too; it dates current events),
   the effective briefing and the glossary-check / proofread `report.md`
   (their web findings, reused). Everything goes inline in the prompt.
2. **Answer** `NotesAnswer`: `{block, term, text, evidence}` per note.
   Validation (repair round): the block exists, once; `term` is written in
   that block's ja or zh (whitespace ignored); `text` one line that the
   layer sets in ≤ `MAX_ROWS` (2) rows (`render.rows`; owner's call: never
   three), measured after `note_text` closes it on a full stop the agent
   left off (in code, not the prompt); evidence non-empty.
3. **`to_notes`**: start = the block's start, shown `FIND_S` + chars /
   `READ_CHARS_PER_S` within 3–10 s; a note due while the previous is up
   cuts it short once that has been up `MIN_SHOWN_S`, else starts when it
   leaves (no drift far from its block). Deliverable `.subs/notes.cht.json` (`Notes`), hand-editable;
   `grill reset <id> --only notes` writes it again.
4. **`grill notes <dir|id>`** runs the stage on a finished or archived
   project (`as_delivery_step`, `registry.NOTES_ONLY`), then the compose step.
5. **Probe** (`grill notes --probe`, `registry.NOTES_PROBE`): the same
   instructions with `probe.md` instead of `answer.md`, no `WEB_SEARCH`;
   every candidate weighed with `annotate` and a reason, into
   `.work/16_notes/probe.json` + `probe.md`. Not in the ledger, nothing
   delivered. The owner tunes "when a note is needed" often; measure a
   prompt change with probes on kept episodes (they spend quota: ask first,
   report the cost) instead of full runs.

## Prompts (`prompts/`)

`notes.md` = the viewer, the inputs, and **"When a note is needed"**: the one
section to tune (show-agnostic; a show's rules go in `instruction.notes`).
`answer.md` = research and writing rules of a real run; `probe.md` = the dry
run. Owner's calls: a subtitle that only labels a fact (「涉嫌洛杉磯命案」)
is not an explanation; a translated card counts as explained, so a note adds
only what it leaves out; the conversation explaining it nearby means no note;
never explain the joke itself; never note who a cast member is (only where
they stood at air time).

## Layer (`render.py`)

`※` + text, 40 px, fixed at the top centre of the picture (`TOP`) whatever
on-screen text is there (owner's call: one place for the eye, like the
dialogue; avoiding cards pushed notes to mid-picture). Each row is its own
event `PITCH` (font size + 2 × `YPAD` = 46) apart on libass's own box
(`BorderStyle` 3, `PAD` 8 left/right; `YPAD` 3 above/below as each event's
`\ybord`, the one override besides `\pos`: a style has one outline, and the
owner wants the rows close), so every row's box fits its glyphs and two rows'
boxes meet without overlapping; an overlap, also between the rows of one
`\N` event, draws the translucent box twice (measured; a drawn
box from estimated widths was 50 px too wide on Latin and misjudged the row
height; rounded corners were dropped for this, owner's call). Rows break at
`ROOM` (1150 px, ~32 characters) preferring a pause (`uneven` 0.5). The only
style lives in the header; events carry only `\pos` and `\ybord`. Packaging burns it after the telops, under the dialogue
(`assemble.with_notes`, `.work/package/notes.ass`); `--no-notes` on
`grill package` leaves it out. The compose delivery step adds it to
`video.cht.ass` (postprocess-and-packaging).
