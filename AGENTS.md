# AGENTS.md

This file is the first-glance handoff for any agent working in this repository.
It is intentionally short — the detailed architecture lives in the domain
skills under `.agents/skills/`. Pick the skill that owns the files you are
touching (paths under `src/grillmaster/`):

| Touching…                                                                 | Read first |
|---------------------------------------------------------------------------|------------|
| `pipeline/`, `stages/` (stage API, metadata → transcript stages), `project/`, `config/`, `events/`, `tui/`, `cli/`, `core/`, `sources/`, `media/`, `asr/`, `glossary/` | **project-architecture** |
| `agents/` (runner, adapters, schema, repair, sessions), `agent_tools/` (MCP `get_frames`, `check_srt`) | **agent-orchestration** |
| `translate/`, `stages/prepass.py`, `stages/chunks.py`, `core/briefing.py` | **translate-pipeline** |
| `postprocess/`, `extras/`, `subtitles/`, `package/`, their stages, side tasks, delivery | **postprocess-and-packaging** |
| `live_chat/`, `stages/chat_fetch.py`, `stages/chat_translate.py` (`--chat`) | **live-chat** |

For a change that spans packages (new stage, new setting, new platform), start
with **project-architecture** — it holds the pipeline contract and the
repo-wide invariants.

## What this project is

**Owarai GrillMaster** — a single-user CLI that downloads a Japanese
variety-show video (by ID or URL) and produces Traditional Chinese subtitles
(SRT + styled ASS), optionally burning them into the video. No server, queue,
or database: all state lives in `projects/<id>/project.json`, and the pipeline
is a declarative, **resumable** stage registry — re-running an ID resumes where
it left off.

Pipeline at a glance (stage keys, in order; `work/NN_<key>/` per stage with
intermediates):
`metadata → download → combine → (chat_fetch) → audio → asr → transcript →
prepass → chunks → refine → glossary → finalize → (chat_translate)`, then
`(archive)` and delivery `(package)`, which reads the archived copy. Side
tasks `(cover)` and `(date_research)` run beside the stages. Parentheses
mark optional steps.

Commands (`bin/grill.bat` is the PATH launcher; `scripts/` holds dev tools):
`grill <SOURCE> [HINT]` (= `grill run`), `grill serial`,
`grill package`, `grill archive`, `grill reset <id> --from|--only <stage>`,
`grill status`, `grill doctor`. Entry point `grillmaster.cli:main`.

Layering: packages may import only downward in the `.importlinter` layer
order, and domain packages never import `project` or `config` — only
`stages/` binds them.

## Environment & tooling

- **Python 3.13+**, managed with **`uv`** + a local **`.venv`**; `uv sync`
  installs the package editable with the `dev` group (pytest, ruff,
  basedpyright, import-linter, poe).
- **FFmpeg** on `PATH`; the agent CLIs (`agy`, `codex`; Claude runs through the
  SDK) logged in on their subscriptions. Only ElevenLabs ASR is metered.
- Config: `grill.toml` (see `grill.example.toml`, schema `grill.schema.json`);
  `.env` holds secrets only (`ELEVENLABS_API_KEY`).

### Definition of done

```bash
uv run poe check                    # fmt-check, ruff, basedpyright, import-linter, pytest
uv run pytest tests/core            # one package
uv run pytest -k chunk              # by keyword
```

`uv run poe check` must pass before a change is done; there is no CI. Tests are
offline and fast; `uv run pytest -m live` (tests/agents/test_live.py) calls the
real agent CLIs and spends quota; `scripts/record_agent_fixture.py` re-records
the adapter contract fixtures. The skills record the reasons behind
non-obvious choices; read them before "fixing" one.

## Keep the docs current (important)

After any change, **update the skill that owns the touched area** so the next
agent inherits an accurate map — a stale skill is worse than no skill. Update
it whenever you:

- add/rename/remove a stage, side task, delivery step, package, agent backend,
  capability or MCP tool;
- change a cross-cutting invariant (resumability via the ledger, stages writing
  only their own outputs, chunk-boundary determinism, caches-never-self-
  invalidate, the layering contracts);
- add or rename a `grill.toml` setting or an `.env` key.

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
