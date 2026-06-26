"""Agent-facing helper instructions that live alongside inference backends.

``get_frames.py`` is a standalone CLI the agent backends run to pull video
frames at specific timestamps on demand; ``build_frame_tool_instruction``
renders the system-prompt block that teaches an agent backend when and how to
call it. Stage helpers may also render non-CLI agent capability guidance such as
web search. These instructions are appended only for agent backends
(gemini-cli / gemini-agy / codex / claude); gemini-api never sees them, so its
prompt stays byte-stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .get_frames import FrameToolStage

_TOOL_DIR = Path(__file__).parent
FRAME_TOOL_SCRIPT: Path = (_TOOL_DIR / "get_frames.py").resolve()
FRAME_TOOL_SCRIPTS: dict[FrameToolStage, Path] = {
    FrameToolStage.PRE_PASS: (
        _TOOL_DIR / "get_frames_for_pre_pass.py"
    ).resolve(),
    FrameToolStage.CHUNK: (_TOOL_DIR / "get_frames_for_chunk.py").resolve(),
    FrameToolStage.REFINE: (_TOOL_DIR / "get_frames_for_refine.py").resolve(),
    FrameToolStage.GLOSSARY_CHECK: (
        _TOOL_DIR / "get_frames_for_glossary_check.py"
    ).resolve(),
}

# Mirror of get_frames.py `_MAX_FRAMES_PER_CALL`, surfaced in the instruction so
# the agent knows the per-call cap.
_MAX_FRAMES_PER_CALL = 20

__all__ = [
    "FRAME_TOOL_SCRIPT",
    "FRAME_TOOL_SCRIPTS",
    "FrameToolStage",
    "build_chunk_frame_tool_instruction",
    "build_frame_tool_instruction",
    "build_glossary_check_frame_tool_instruction",
    "build_pre_pass_agent_instruction",
    "build_pre_pass_frame_tool_instruction",
    "build_pre_pass_web_search_instruction",
    "build_refine_frame_tool_instruction",
    "frame_tool_command_prefix",
    "frame_tool_command_prefixes",
]


def frame_tool_command_prefix(stage: FrameToolStage) -> str:
    """Return the stable shell command prefix used for Gemini CLI policy."""
    python = Path(sys.executable).resolve()
    return f'& "{python}" "{FRAME_TOOL_SCRIPTS[stage]}"'


def frame_tool_command_prefixes() -> list[str]:
    return [frame_tool_command_prefix(stage) for stage in FrameToolStage]


def build_frame_tool_instruction(
    project_dir: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    scope_label: str,
    stage: FrameToolStage,
) -> str:
    """Render the on-demand frame-tool instruction for an agent backend.

    Embeds the exact stage-specific wrapper command and valid time window, so
    the agent can run it verbatim from its throwaway working directory.

    The generated command is fully pre-filled except for the ``--times`` value.
    Output paths are inferred from ``project_dir`` and ``stage`` by Python code.
    """
    project = project_dir.resolve()
    start = max(0.0, start_seconds)
    end = max(start, end_seconds)
    command = (
        f'{frame_tool_command_prefix(stage)} --project-dir "{project}" '
        f'--times "62.5,70,77"'
    )
    return (
        "## On-demand video frames\n"
        "The pre-sampled reference images may not cover the exact moment you "
        "need. When a line is doubtful — garbled or suspicious ASR, an unclear "
        "proper noun or name, an on-screen text card (字卡, which Japanese "
        "variety shows often flash exactly at these moments), or you simply "
        "want to confirm what is on screen — extract the exact frames you need "
        "instead of guessing.\n\n"
        "Run this command with the specific timestamps (in seconds) you want to "
        "see; replace only the value after `--times`. It writes frames into the "
        "stage-local `extra_frames` directory and prints image paths which you "
        "then open with your file/image-reading tool:\n\n"
        f"```\n{command}\n```\n\n"
        f"- Valid timestamps for {scope_label}: {start:.3f}s to {end:.3f}s. "
        "Stay strictly within this window.\n"
        f"- At most {_MAX_FRAMES_PER_CALL} timestamps per call.\n"
        "- Use this when visual evidence would clarify names, captions, "
        "objects, reactions, scene changes, or any decision that would be "
        "weaker if based on text alone."
    )


def build_pre_pass_frame_tool_instruction(
    project_dir: Path,
    start_seconds: float,
    end_seconds: float,
) -> str:
    return (
        build_frame_tool_instruction(
            project_dir,
            start_seconds,
            end_seconds,
            scope_label="the entire video",
            stage=FrameToolStage.PRE_PASS,
        )
        + "\n\n"
        "Pre-pass is the anchor for the entire downstream translation. Use this "
        "tool proactively before finalizing `proper_nouns`, `glossary`, "
        "`catchphrases`, or `segment_summaries` when the sparse reference "
        "images do not verify a visual fact. Fetch extra frames for likely "
        "on-screen names/titles, inserted captions, lower-thirds, scoreboards, "
        "props, costumes, locations, scene changes, or visual gags that could "
        "affect downstream consistency. Do not guess stable visual anchors from "
        "ASR alone when an extra frame can verify them."
    )


def build_pre_pass_web_search_instruction() -> str:
    return (
        "## Agent web search\n"
        "Use local evidence first: program title/description, full audio when "
        "available, reference images, fixed glossary if supplied, parent "
        "pre-pass context if supplied, and the source SRT as a fallible "
        "timing/text scaffold. The SRT is not ground truth: expect ASR errors "
        "and resolve conflicts with audio, images, program metadata, and "
        "reliable external references when needed.\n\n"
        "Use built-in web search only when local context is insufficient for an "
        "external fact that would materially affect `characters`, "
        "`proper_nouns`, `glossary`, `catchphrases`, or `segment_summaries`: "
        "official/common spellings of talent or group names, program or segment "
        "titles, public work titles, brand names, recurring public catchphrases, "
        "or other public references.\n\n"
        "Prefer official pages, reliable listings, or stable public references "
        "over unsourced snippets. If web evidence is inconclusive, keep the "
        "mapping conservative instead of inventing a confident localization. "
        "Do not use web search for routine phrasing or tone choices already "
        "settled by local evidence. Output remains the schema JSON only: do not "
        "add citations, prose, or markdown."
    )


def build_pre_pass_agent_instruction(
    project_dir: Path,
    start_seconds: float,
    end_seconds: float,
) -> str:
    return (
        build_pre_pass_frame_tool_instruction(
            project_dir,
            start_seconds,
            end_seconds,
        )
        + "\n\n"
        + build_pre_pass_web_search_instruction()
    )


def build_chunk_frame_tool_instruction(
    project_dir: Path,
    start_seconds: float,
    end_seconds: float,
) -> str:
    return build_frame_tool_instruction(
        project_dir,
        start_seconds,
        end_seconds,
        scope_label="your assigned chunk range",
        stage=FrameToolStage.CHUNK,
    )


def build_refine_frame_tool_instruction(
    project_dir: Path,
    start_seconds: float,
    end_seconds: float,
) -> str:
    return (
        build_frame_tool_instruction(
            project_dir,
            start_seconds,
            end_seconds,
            scope_label="the entire video",
            stage=FrameToolStage.REFINE,
        )
        + "\n\n"
        "Refine is still a medium polishing pass, not a full retranslation. "
        "Use frames proactively for concrete subtitle decisions where visual "
        "evidence can change the wording: on-screen captions, visible names or "
        "titles, props, scoreboards, reaction shots, visual gags, or conflicts "
        "between the Japanese source and Chinese baseline. Do not spend frame "
        "calls on routine fluency edits that text context already settles."
    )


def build_glossary_check_frame_tool_instruction(
    project_dir: Path,
    start_seconds: float,
    end_seconds: float,
) -> str:
    return (
        build_frame_tool_instruction(
            project_dir,
            start_seconds,
            end_seconds,
            scope_label="the entire video",
            stage=FrameToolStage.GLOSSARY_CHECK,
        )
        + "\n\n"
        "Glossary check is the terminology and factual-consistency pass. Use "
        "it a little more proactively here than in ordinary polishing, because "
        "this is the final translation safety net before finalize. If a "
        "suspicious name, group, title, catchphrase, on-screen caption, visual "
        "gag, or possible `pre_pass.json` correction lines up with a known "
        "timestamp, fetch the relevant frame instead of relying on subtitle "
        "text alone. You do not need frames for every edit, but any correction "
        "that would be meaningfully stronger with visual evidence should use "
        "them."
    )
