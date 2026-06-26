"""Subprocess wrapper for the Antigravity CLI (``agy``).

A pure ``text + 0..N images -> text`` wrapper, sibling to ``gemini_cli``. Auth
is delegated to agy's own login (subscription); the parent process's paid
API-key env vars are scrubbed so it never silently falls back to metered
billing.

Two things make ``agy`` different from every other backend and shape this
module:

* **Non-TTY stdout drop.** ``agy -p`` emits nothing when stdout is not a
  terminal (exit 0, empty output). So the CLI is spawned under a pseudo-terminal
  (ConPTY via ``pywinpty`` on Windows, stdlib ``pty`` on POSIX) and its raw
  terminal bytes are captured, then cleaned of ANSI/spinner chrome.
* **Prompt is a CLI arg, not stdin.** ``agy -p "<prompt>"`` takes the prompt as
  an argument, and the full SRT is far too large for a Windows argv. So the real
  prompt is written to ``INPUT.md`` in a staged workspace and a tiny bootstrap
  arg tells the agent to read it. Images are staged alongside.

Like ``gemini_cli`` this is a pure text generator: it never enforces a response
schema. Schema handling (the JSON-Schema suffix + validate-and-repair loop) is
owned by ``run_inference`` and shared across every prompt-based backend.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from .base import (
    DEFAULT_TIMEOUT_SECS,
    InferenceError,
    InferenceNotInstalledError,
)

# Hardcoded per-file size guard for staged images, mirroring gemini_cli.
_MAX_MEDIA_FILE_MB = 20

# Antigravity CLI executable name. Hardcoded maintainer constant.
_CLI_EXECUTABLE = "agy"

# Paid API-key env vars agy might prefer over its cached subscription login.
# Scrubbed so subscription auth is used (the whole reason for this backend).
# ANTIGRAVITY_API_KEY is intentionally NOT scrubbed — it is agy's own key.
_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The staged prompt file the bootstrap arg points agy at.
_PROMPT_FILE_NAME = "INPUT.md"

# Sentinel markers the agent is told to wrap its final answer in, so the answer
# can be sliced out of interleaved tool logs / banner chrome. Extraction falls
# back to the whole cleaned output when the markers are absent.
_BEGIN_MARKER = "<<<AGY_BEGIN>>>"
_END_MARKER = "<<<AGY_END>>>"

# agy bakes the reasoning effort into the model name (it has no separate effort
# flag), and its --model value is the exact display string `agy models` prints.
# To keep this backend's config consistent with gemini-api / gemini-cli (which
# take ID-form models like "gemini-3.1-pro" plus a low/medium/high effort), we
# map (model id, effort) -> agy display name here. Only the Gemini models agy
# exposes are wired; each maps to the efforts agy actually offers for it.
_AGY_MODEL_BASES = {
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.1-pro": "Gemini 3.1 Pro",
}
_AGY_EFFORTS = {"low": "Low", "medium": "Medium", "high": "High"}
_AGY_VALID_EFFORTS = {
    "Gemini 3.5 Flash": {"Low", "Medium", "High"},
    "Gemini 3.1 Pro": {"Low", "High"},
}

# ANSI CSI/OSC escape sequences and the spinner/box-drawing glyphs agy paints
# while "thinking". Stripped from the raw terminal capture.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷╭╮╰╯│─"


class GeminiAgyError(InferenceError):
    """Raised when the Antigravity CLI fails, times out, or misbehaves."""


class GeminiAgyNotInstalledError(GeminiAgyError, InferenceNotInstalledError):
    """Raised when the agy executable is not on PATH."""


class GeminiAgyQuotaError(GeminiAgyError):
    """Raised when agy reports a 429 / quota-exhausted error."""


class GeminiAgyResult(BaseModel):
    """Outcome of one ``run_gemini_agy`` call.

    agy has no machine-readable result envelope (no ``--output-format json``),
    so there are no per-call request/cost stats to recover: ``requests`` is
    always 1 and cost is handled as 0 by the caller (subscription backend).
    """

    response: str
    requests: int = 1


def _scrubbed_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in _API_KEY_ENV_VARS:
        env.pop(key, None)
    return env


def clean_terminal_output(raw: str) -> str:
    """Strip ANSI escapes, ``\\r`` repaints, and spinner/box glyphs.

    agy's pseudo-terminal output is full of color codes, cursor movements, and
    spinner overwrites. Collapse each ``\\r``-delimited segment to its final
    repaint and drop the decorative glyphs, leaving readable text.
    """
    no_ansi = _ANSI_RE.sub("", raw)
    lines: list[str] = []
    for line in no_ansi.split("\n"):
        # A carriage return repaints the line in place; keep only the last
        # non-empty paint (a trailing \r is just cursor positioning).
        segment = line.rstrip("\r").split("\r")[-1]
        segment = segment.translate({ord(ch): None for ch in _SPINNER_CHARS})
        lines.append(segment.rstrip())
    return "\n".join(lines)


def slice_marked_answer(text: str) -> str:
    """Return the content between the sentinel markers, else the whole text."""
    start = text.find(_BEGIN_MARKER)
    end = text.rfind(_END_MARKER)
    if start != -1 and end != -1 and end > start:
        return text[start + len(_BEGIN_MARKER) : end].strip()
    return text.strip()


def resolve_agy_model(model: str, reasoning_effort: str) -> str:
    """Map an ID-form model + effort to agy's exact ``--model`` display string.

    e.g. ``("gemini-3.5-flash", "high") -> "Gemini 3.5 Flash (High)"``. Raises
    ``GeminiAgyError`` for an unknown model or an effort that model does not
    expose, listing the valid combinations so config typos fail loudly.
    """
    base = _AGY_MODEL_BASES.get(model.strip().lower())
    effort = _AGY_EFFORTS.get(reasoning_effort.strip().lower())
    valid = base is not None and effort in _AGY_VALID_EFFORTS.get(base, set())
    if not valid:
        supported = ", ".join(
            f"{b} ({e})"
            for b, efforts in _AGY_VALID_EFFORTS.items()
            for e in ("Low", "Medium", "High")
            if e in efforts
        )
        raise GeminiAgyError(
            f"unsupported gemini-agy model/effort: {model!r}/{reasoning_effort!r}. "
            f"Use an id form like 'gemini-3.1-pro' or 'gemini-3.5-flash/high'. "
            f"Resolvable agy models: {supported}."
        )
    return f"{base} ({effort})"


def _classify_error(message: str) -> GeminiAgyError:
    haystack = message.lower()
    is_quota = any(
        marker in haystack
        for marker in (
            "429",
            "quota",
            "exhaust",
            "rate limit",
            "resource_exhausted",
        )
    )
    if is_quota:
        return GeminiAgyQuotaError(message)
    return GeminiAgyError(message)


def _run_under_pty(
    argv: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
) -> str:
    """Spawn ``argv`` attached to a pseudo-terminal and return raw output.

    A real (pseudo) terminal is required: ``agy -p`` writes nothing to a plain
    pipe. The platform branch is only the pty allocator; capture + timeout
    handling is shared in spirit.
    """
    cwd_str = str(cwd) if cwd is not None else None

    if os.name == "nt":
        try:
            from winpty import PtyProcess  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise GeminiAgyError(
                "pywinpty is required for the gemini-agy backend on Windows "
                "but is not installed (pip install pywinpty)."
            ) from exc

        proc = PtyProcess.spawn(argv, cwd=cwd_str, env=env)
        chunks: list[str] = []

        def _drain() -> None:
            try:
                while True:
                    data = proc.read(4096)
                    if data:
                        chunks.append(data)
                    elif not proc.isalive():
                        break
            except EOFError:
                pass

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            try:
                proc.terminate(force=True)
            finally:
                pass
            raise GeminiAgyError(f"agy timed out after {timeout}s")
        return "".join(chunks)

    # POSIX
    import pty
    import select
    import subprocess
    import time

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is built from trusted parts
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd_str,
            env=env,
            close_fds=True,
        )
    finally:
        os.close(slave)

    chunks_b: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise GeminiAgyError(f"agy timed out after {timeout}s")
            ready, _, _ = select.select([master], [], [], min(remaining, 1.0))
            if master in ready:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks_b.append(data)
            elif proc.poll() is not None:
                break
    finally:
        os.close(master)
        proc.wait()
    return b"".join(chunks_b).decode("utf-8", errors="replace")


def _bootstrap_prompt(prompt_file: Path, image_files: list[Path]) -> str:
    """The ``-p`` argument, built from agy's native ``@<path>`` attach tokens.

    ``@<file>`` pulls a file straight into the model's context — text inlined,
    images as native vision — the same mechanism gemini-cli uses, so the agent
    never has to issue a file-read tool call to "find" the inputs (which it
    otherwise stalls on). Absolute paths are used so ``@`` resolution does not
    depend on the process cwd; the staged files live under an ``--add-dir``'d
    workspace so agy can read them.
    """
    images = (
        " The reference images for this task are attached here: "
        + " ".join(f"@{img}" for img in image_files)
        if image_files
        else ""
    )
    return (
        f"Follow the instructions in @{prompt_file} exactly and treat that "
        f"file's entire contents as your task.{images} "
        "Then print your final answer between a line containing only "
        f"{_BEGIN_MARKER} and a line containing only {_END_MARKER}, with "
        f"nothing after {_END_MARKER}."
    )


def run_gemini_agy(
    prompt: str,
    *,
    model: str,
    reasoning_effort: str = "high",
    images: list[Path] | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> GeminiAgyResult:
    """Invoke the Antigravity CLI once and return the parsed result.

    ``model`` + ``reasoning_effort`` use the same id form as gemini-api /
    gemini-cli (e.g. ``"gemini-3.1-pro"`` + ``"high"``) and are mapped here to
    agy's ``--model`` display string via :func:`resolve_agy_model` (agy has no
    separate effort flag — the effort lives in the model name).

    The real ``prompt`` is written to ``INPUT.md`` in a staged workspace (agy
    takes its prompt as an argv arg, which the full SRT would overflow) and
    images are staged beside it; the ``-p`` arg then references both with agy's
    native ``@<path>`` attach tokens so the file text and images land directly in
    the model's context (no agent file-read round-trip).

    ``cwd`` becomes the agent's working directory when given (so file-writing
    postprocess stages edit the project tree); otherwise the staged workspace is
    used. ``--add-dir`` grants read access to the workspace (so the ``@`` tokens
    resolve), the repo root (for the on-demand frame tool script), and ``cwd``.
    ``--dangerously-skip-permissions`` lets the agent run that frame tool,
    matching codex ``--yolo`` / claude ``bypassPermissions`` (agy has no
    fine-grained policy file).
    """
    images = images or []

    executable = shutil.which(_CLI_EXECUTABLE)
    if executable is None:
        raise GeminiAgyNotInstalledError(
            f"Antigravity CLI executable not found on PATH: {_CLI_EXECUTABLE!r}"
        )

    agy_model = resolve_agy_model(model, reasoning_effort)

    limit_bytes = _MAX_MEDIA_FILE_MB * 1024 * 1024
    for img in images:
        if not img.exists():
            raise GeminiAgyError(f"agy image file not found: {img}")
        size = img.stat().st_size
        if size > limit_bytes:
            raise GeminiAgyError(
                f"Image file exceeds {_MAX_MEDIA_FILE_MB} MB limit "
                f"({size / 1024 / 1024:.1f} MB): {img}"
            )

    effective_timeout = timeout or DEFAULT_TIMEOUT_SECS

    workspace = Path(tempfile.mkdtemp(prefix="gemini_agy_"))
    try:
        prompt_file = workspace / _PROMPT_FILE_NAME
        prompt_file.write_text(prompt, encoding="utf-8")

        staged_images: list[Path] = []
        for index, src in enumerate(images):
            staged = workspace / f"{index:02d}_{src.name}"
            shutil.copy2(src, staged)
            staged_images.append(staged)

        # Workspace + repo root + cwd are the agent's accessible roots.
        add_dirs: list[Path] = [workspace, _REPO_ROOT]
        if cwd is not None:
            add_dirs.append(cwd.resolve())
        deduped: list[Path] = []
        seen: set[str] = set()
        for directory in add_dirs:
            key = str(directory)
            if key not in seen:
                seen.add(key)
                deduped.append(directory)

        cmd: list[str] = [
            executable,
            "--print",
            _bootstrap_prompt(prompt_file, staged_images),
            "--model",
            agy_model,
            "--dangerously-skip-permissions",
            "--print-timeout",
            f"{effective_timeout}s",
        ]
        for directory in deduped:
            cmd += ["--add-dir", str(directory)]

        process_cwd = cwd.resolve() if cwd is not None else workspace
        logger.debug(
            f"Running agy: model={model!r}/{reasoning_effort!r} -> "
            f"{agy_model!r} prompt_chars={len(prompt)} "
            f"(via {_PROMPT_FILE_NAME}) images={len(staged_images)} "
            f"cwd={process_cwd} timeout={effective_timeout}s"
        )

        raw = _run_under_pty(
            cmd,
            cwd=process_cwd,
            env=_scrubbed_env(),
            timeout=effective_timeout,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    cleaned = clean_terminal_output(raw)
    if not cleaned.strip():
        raise GeminiAgyError("agy produced no usable output (empty after clean)")

    # Surface obvious quota/auth errors that agy prints as plain text.
    lowered = cleaned.lower()
    if _BEGIN_MARKER not in cleaned and any(
        marker in lowered
        for marker in ("quota", "resource_exhausted", "rate limit", "error:")
    ):
        raise _classify_error(cleaned[-1000:])

    response = slice_marked_answer(cleaned)
    logger.debug(f"agy ok: response_chars={len(response)}")
    return GeminiAgyResult(response=response, requests=1)


# Re-exported so a future code path can detect the optional pty dependency
# without importing winpty at module load on non-Windows hosts.
__all__ = [
    "GeminiAgyError",
    "GeminiAgyNotInstalledError",
    "GeminiAgyQuotaError",
    "GeminiAgyResult",
    "clean_terminal_output",
    "resolve_agy_model",
    "run_gemini_agy",
    "slice_marked_answer",
]
