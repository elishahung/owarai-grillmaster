"""`grill doctor`: are the external tools and `grill.toml` in order?"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import typer

from grillmaster.core.model_spec import Backend

_VERSION_TIMEOUT_S = 30.0
# The Claude backend runs the CLI bundled with the SDK, not one on PATH.
_CLAUDE_SDK_DISTRIBUTION = "claude-agent-sdk"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    # A failed optional check is reported but does not fail the command.
    required: bool = True


def doctor_command() -> None:
    """Check ffmpeg/ffprobe, the agent CLIs and grill.toml."""
    from grillmaster.config.errors import ConfigError
    from grillmaster.config.load import load_config

    checks: list[Check] = []
    used_backends = set(Backend)
    try:
        loaded = load_config()
    except ConfigError as error:
        checks.append(Check("grill.toml", ok=False, detail=str(error)))
    else:
        checks.append(Check("grill.toml", ok=True, detail=str(loaded.path)))
        roles = loaded.config.agents.roles.specs()
        used_backends = {spec.backend for spec in roles.values()}
    # Each probe starts a process; run them side by side, reporting in order.
    with ThreadPoolExecutor() as pool:
        checks += pool.map(_ffmpeg_check, ("ffmpeg", "ffprobe"))
        checks += [
            replace(check, required=backend in used_backends)
            for backend, check in zip(
                Backend, pool.map(_backend_check, Backend), strict=True
            )
        ]
    for check in checks:
        status = "ok" if check.ok else ("FAIL" if check.required else "warn")
        typer.echo(f"{status:<5}{check.name:<12}{check.detail}")
    if any(check.required and not check.ok for check in checks):
        raise typer.Exit(code=1)


def _ffmpeg_check(program: str) -> Check:
    from grillmaster.agents.events import first_line
    from grillmaster.media.errors import MediaError
    from grillmaster.media.ffmpeg import SubprocessFfmpegRunner

    try:
        output = SubprocessFfmpegRunner().run(
            [program, "-version"], timeout=_VERSION_TIMEOUT_S
        )
    except MediaError as error:
        return Check(program, ok=False, detail=first_line(str(error)))
    return Check(program, ok=True, detail=first_line(output))


def _backend_check(backend: Backend) -> Check:
    from importlib.metadata import PackageNotFoundError, version

    from grillmaster.agents.adapters._jsonl import resolve_cli
    from grillmaster.agents.errors import AgentConfigError
    from grillmaster.agents.events import first_line
    from grillmaster.agents.process import ProcessSpec, TimeoutExpired, run_text

    name = backend.value
    if backend is Backend.CLAUDE:
        try:
            sdk = version(_CLAUDE_SDK_DISTRIBUTION)
        except PackageNotFoundError:
            return Check(name, ok=False, detail="claude-agent-sdk is not installed")
        return Check(name, ok=True, detail=f"{_CLAUDE_SDK_DISTRIBUTION} {sdk}")
    try:
        executable = resolve_cli(name, None)
    except AgentConfigError as error:
        return Check(name, ok=False, detail=str(error))
    spec = ProcessSpec(
        [executable, "--version"], cwd=None, timeout_s=_VERSION_TIMEOUT_S
    )
    try:
        result = run_text(spec)
    except (OSError, TimeoutExpired) as error:
        return Check(name, ok=False, detail=str(error))
    if result.returncode != 0:
        detail = first_line(result.stderr) or f"exit {result.returncode}"
        return Check(name, ok=False, detail=detail)
    return Check(name, ok=True, detail=first_line(result.stdout) or executable)
