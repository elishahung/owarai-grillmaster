# AGENTS.md

This file is the first-glance handoff for any agent working in this repository.
It is intentionally short — the **detailed architecture lives in the
`project-architecture` skill** (`.agents/skills/project-architecture/SKILL.md`).
Read that skill before any non-trivial change under `services/`, `workflow.py`,
`project.py`, or `settings.py`.

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
  backends are selectable per stage (`gemini-api` / `gemini-cli` / `claude` /
  `codex`); only `gemini-api` is metered.

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

After any change to the codebase, **check whether the `project-architecture`
skill needs updating** so the next agent inherits an accurate map. Update
`.agents/skills/project-architecture/SKILL.md` whenever you:

- add/rename/remove a pipeline stage, service module, or model backend;
- change a cross-cutting invariant (resumability, stage↔field sync, chunk-
  boundary determinism, cover-always-Codex, prompt-cache byte-stability);
- add or rename a setting or an `.env` key.

The project is small enough today to document in this one skill. As it grows,
the intended path is to **split the codebase into multiple focused skills** (one
per subsystem) so an agent reads only the skill relevant to the part it is
changing — rather than loading one giant document. If you find yourself adding a
large, self-contained subsystem, factor it into its own skill instead of bloating
`project-architecture`.
