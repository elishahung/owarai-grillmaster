"""Agent-driven structural repair for broken chunk SRT outputs.

When a translated chunk fails `validate_chunk_structure`, we hand the problem
to a coding agent (Codex or Claude, per `settings.agent_postprocess_backend`):
it gets the authoritative source SRT, the broken output, and a validator command
it runs itself, iterating until the output matches the source skeleton. The
Python worker re-validates the agent's `fixed.srt` as a final guard.

This mirrors the `services.postprocess` task pattern: a thin orchestrator over
`services.inference.run_inference` plus a `prompts/*.md` system prompt. The
sync backend is invoked through `asyncio.to_thread` so it composes with the
worker's async/concurrent translation loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from settings import settings
from services.inference import Backend, run_inference


class ChunkFixError(RuntimeError):
    """Raised when the agent fails to produce a repaired chunk SRT."""


_PROMPT = (Path(__file__).parent / "prompts" / "structural_fix.md").read_text(
    encoding="utf-8"
)
_VALIDATOR = (Path(__file__).parent / "validate_chunk.py").resolve()


def _concrete_section(error: str) -> str:
    """Append the run-specific files, validator command, and head-start error."""
    return (
        "\n\n## This task\n\n"
        f"Initial validation error to fix:\n{error}\n\n"
        "Validator command (run from the current working directory; iterate "
        "until it prints `VALID`):\n\n"
        "```\n"
        f'python "{_VALIDATOR}" source.srt fixed.srt\n'
        "```\n"
    )


async def fix_chunk_structure(
    source_srt: str,
    broken_output: str,
    error: str,
    workspace_dir: Path,
    log_prefix: str = "",
) -> str:
    """Repair `broken_output` to match `source_srt` via an agent; return the SRT.

    Writes `source.srt`/`broken.srt` into `workspace_dir` (the agent's cwd) and
    expects the agent to produce `fixed.srt` there. Raises `ChunkFixError` if no
    fixed file is produced. The caller is responsible for the final
    `validate_chunk_structure` guard.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "source.srt").write_text(source_srt, encoding="utf-8")
    (workspace_dir / "broken.srt").write_text(broken_output, encoding="utf-8")
    fixed_path = workspace_dir / "fixed.srt"
    # Drop any stale artifact so a crashed prior run cannot masquerade as success.
    fixed_path.unlink(missing_ok=True)

    prompt = _PROMPT + _concrete_section(error)
    backend = Backend(settings.agent_postprocess_backend)
    logger.info(
        f"{log_prefix} Invoking {backend.value} to repair chunk structure "
        f"in {workspace_dir}"
    )
    spec = settings.agent_postprocess_model
    await asyncio.to_thread(
        run_inference,
        backend=backend,
        prompt=prompt,
        cwd=workspace_dir,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
    )

    if not fixed_path.exists():
        raise ChunkFixError(f"agent did not produce fixed.srt: {fixed_path}")
    return fixed_path.read_text(encoding="utf-8-sig")
