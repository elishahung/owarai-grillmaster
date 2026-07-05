# AGENTS.md

This file is the first-glance handoff for any agent working in this repository.
It is intentionally short — the detailed architecture lives in the domain
skills under `.agents/skills/`. Pick the skill that owns the files you are
touching:

| Touching…                                                                 | Read first |
|---------------------------------------------------------------------------|------------|
| `workflow.py`, `project.py`, `settings.py`, `main.py`, or `services/` srt / media / ytdlp / elevenlabs / fixed_glossary / progress | **project-architecture** |
| `services/inference/` (backends, schema repair, frame tools)              | **inference-layer** |
| `services/translate/` (pre-pass, chunking, chunk workers, caches, prompts) | **translate-pipeline** |
| `services/postprocess/`, `services/finalize/`, `services/package/`        | **postprocess-and-packaging** |

For a change that spans modules (new stage, new setting, new platform), start
with **project-architecture** — it holds the orchestration contract and the
repo-wide invariants.

## What this project is

**Owarai GrillMaster** — a single-user CLI that downloads a Japanese
variety-show video (by ID or URL) and produces Traditional Chinese subtitles
(SRT + styled ASS), optionally burning them into the video. No server, queue, or
database: all state lives in `projects/<id>/project.json`, and the pipeline is a
linear, idempotent, **resumable** stage machine — re-running an ID resumes where
it left off.

Pipeline at a glance:
`download → combine → extract audio → ASR (ElevenLabs) → pre-pass analysis →
concurrent chunk translation → (refine) → (glossary check) → finalize (ASS+SRT)
→ (archive) → (package)`. Stages in parentheses are optional.

Entry point: `main.py` (Typer CLI) → `workflow.submit_project`. Run with
`grill <SOURCE> [HINT]` (via `scripts/grill.bat` on PATH) or
`python main.py <SOURCE> [HINT]`.

## Environment & tooling

- **Python 3.13+**, managed with **`uv`** + a local **`.venv`**. Install deps
  with `uv sync` (or `pip install -e .`).
- **FFmpeg** must be installed and on `PATH` (media combine/extract/burn-in).
- Config via a `.env` file (see `README.md` for the full key list). Model
  backends are selectable per stage (`gemini-api` / `gemini-cli` / `gemini-agy`
  / `claude` / `codex`); only `gemini-api` is metered.

### Running tests

```bash
uv run --with pytest python -m pytest                       # full suite
uv run --with pytest python -m pytest tests/test_srt.py     # single file
uv run --with pytest python -m pytest -k chunk_validation   # by keyword
```

Use `python -m pytest` (not bare `pytest`) so the repo root is on `sys.path`.
pytest is **not** a project dependency — `uv run --with pytest` pulls it in
ephemerally. Tests are offline (network/model calls mocked) and fast; there is
no CI, so run them yourself before considering a change done.

## Keep the docs current (important)

After any change, **update the skill that owns the touched area** so the next
agent inherits an accurate map — a stale skill is worse than no skill. Update
it whenever you:

- add/rename/remove a pipeline stage, service module, or model backend;
- change a cross-cutting invariant (resumability, stage↔field sync, chunk-
  boundary determinism, cover-always-Codex, caches-never-self-invalidate);
- add or rename a setting or an `.env` key.

Keep skill updates proportional. Document facts the next agent must know to
avoid breaking architecture or operating the wrong subsystem: ownership
boundaries, persisted artifact locations, backend/tool contracts, and invariants
that affect future changes. Do **not** promote one-off debugging notes,
implementation minutiae, or local workaround history into top-level guidance;
fold small local details into the relevant existing paragraph, or leave them out
when the code/tests are the clearer source of truth.

If you add a large, self-contained subsystem, factor it into its own skill
under `.agents/skills/` (and add a row to the table above) instead of bloating
an existing one. AGENTS.md itself stays thin — detail belongs in skills.
