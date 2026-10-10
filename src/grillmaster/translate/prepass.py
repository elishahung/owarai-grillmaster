"""The pre-pass: one whole-film analysis call producing the `Briefing`.

The agent sees the full SRT, program context, the whole fixed glossary,
20-40 reference frames, the full audio on audio-capable backends, and the
chunk boundaries. `segment_summaries` is a plain list, so a briefing that
summarizes only the opening chunk is schema-valid, and long episodes really
do come back that way; the coverage validator turns that into a repair round
naming the missing ranges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import load_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.translate import prompt

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.translate.inputs import PrepassInputs

TASK_NAME = "prepass"


def ensure_briefing(
    output: Path, agents: AgentRunner, prepare: Callable[[], AgentTask[Briefing]]
) -> Briefing:
    """The briefing at `output`, analysing (`prepare`, then the agent) and
    writing it on a miss; a missing or unreadable file is a miss."""
    cached = load_model(output, Briefing)
    if cached is not None:
        logger.info(f"Reusing existing briefing {output}")
        return cached
    briefing = agents.run(prepare()).output
    write_model(output, briefing)
    logger.success(
        f"Pre-pass briefing: {len(briefing.characters)} characters, "
        f"{len(briefing.proper_nouns)} proper nouns, "
        f"{len(briefing.glossary)} glossary terms, "
        f"{len(briefing.catchphrases)} catchphrases, "
        f"{len(briefing.segment_summaries)} segment summaries"
    )
    return briefing


def build_prepass_task(
    inputs: PrepassInputs,
    *,
    session_dir: Path,
    workdir: Path,
    tools: ToolSession,
    add_dirs: tuple[Path, ...] = (),
) -> AgentTask[Briefing]:
    """The pre-pass agent call; it may search the web for public facts."""
    assets = inputs.assets
    return AgentTask(
        name=TASK_NAME,
        role=Role.PREPASS,
        instructions=prompt.prepass_instruction(inputs),
        prompt=prompt.prepass_message(inputs),
        session_dir=session_dir,
        workdir=workdir,
        output=SchemaOutput(Briefing),
        images=tuple(frame.path for frame in assets.frames),
        audio=(assets.audio,) if assets.audio is not None else (),
        tools=tools,
        add_dirs=add_dirs,
        validate=segment_coverage_validator(
            [chunk.index_range for chunk in inputs.chunks]
        ),
        requires=frozenset({Capability.WEB_SEARCH}),
    )


def segment_coverage_validator(
    boundaries: Sequence[tuple[int, int]],
) -> Callable[[Briefing], None]:
    """Reject a briefing without one segment summary per chunk range; the
    message quotes back only the missing ranges."""

    def validate(briefing: Briefing) -> None:
        covered = {
            (segment.from_index, segment.to_index)
            for segment in briefing.segment_summaries
        }
        missing = [boundary for boundary in boundaries if boundary not in covered]
        if missing:
            raise ValidationFailure(
                f"segment_summaries 只涵蓋 {len(boundaries) - len(missing)}/"
                f"{len(boundaries)} 個 chunk 區間，每個區間都必須各有一筆。"
                "缺少下列區間，請補齊（from_index／to_index 需完全相符，"  # noqa: RUF001 - full-width prompt text
                f"已有的區間請原樣保留）：{prompt.boundaries_json(missing)}"
            )

    return validate
