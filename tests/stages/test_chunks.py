from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, override

import pytest
from tests.fakes import FakeAgentRunner, FakeFfmpeg, Rounds, make_blocks, make_briefing

from grillmaster.agents.adapters.base import Media, MediaKind
from grillmaster.agents.errors import AgentOutputError
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import SrtBlock, read_srt_file, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.events.progress import CHUNK_PROGRESS_SCOPE
from grillmaster.events.types import (
    ProgressAdvanced,
    ProgressFinished,
    ProgressStarted,
)
from grillmaster.media.errors import MediaError
from grillmaster.stages import chunks
from grillmaster.stages.base import MissingArtifactError
from grillmaster.translate.chunk import ChunkLine, ChunkTranslation, task_name
from grillmaster.translate.chunker import Chunk, split_into_chunks
from grillmaster.translate.errors import TranslateError

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from tests.fakes import RecordingSink
    from tests.stages.conftest import MakeContext

    from grillmaster.agents.task import AgentTask
    from grillmaster.events.types import Event
    from grillmaster.media.ffmpeg import ProgressCallback
    from grillmaster.project.layout import ProjectLayout

_BLOCKS = make_blocks(6)
# Each `make_blocks` block is 38 characters: this limit makes 3 chunks.
_CHAR_LIMIT = 100
_CHUNKS = split_into_chunks(_BLOCKS, _CHAR_LIMIT)


def _translated(chunk: Chunk, prefix: str = "譯") -> ChunkTranslation:
    return ChunkTranslation(
        blocks=[
            ChunkLine(index=block.index, text=f"{prefix} {block.index}")
            for block in chunk.blocks
        ]
    )


@pytest.fixture
def config_sections() -> dict[str, Any]:
    return {"translate": {"chunk_char_limit": _CHAR_LIMIT, "chunk_attempts": 2}}


@pytest.fixture
def script() -> dict[str, object]:
    return {task_name(chunk): _translated(chunk) for chunk in _CHUNKS}


def _voiced(blocks: Sequence[SrtBlock]) -> list[SrtBlock]:
    return [replace(block, text=f"[V{block.index}] {block.text}") for block in blocks]


@pytest.fixture(autouse=True)
def inputs(layout: ProjectLayout) -> None:
    write_srt_file(layout.ja_srt, _BLOCKS)
    write_srt_file(layout.ja_voiced_srt, _voiced(_BLOCKS))
    write_model(
        layout.prepass_briefing, make_briefing(*(c.index_range for c in _CHUNKS))
    )
    layout.video.write_bytes(b"video")
    layout.audio.parent.mkdir(parents=True)
    layout.audio.write_bytes(b"audio")


class AudioFailingFfmpeg(FakeFfmpeg):
    """Fails the audio slice of chunk 3-4 only."""

    @override
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        if "0003-0004" in argv[-1] and "audio.ogg" in argv[-1]:
            raise MediaError("audio slice failed")
        return super().run(
            argv, timeout=timeout, cwd=cwd, on_progress=on_progress, abort=abort
        )


def test_chunk_boundaries_under_test() -> None:
    assert [chunk.index_range for chunk in _CHUNKS] == [(1, 2), (3, 4), (5, 6)]


def test_translates_every_chunk_and_merges(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    merged = read_srt_file(layout.merged_srt)
    assert merged == [
        SrtBlock(block.index, block.timecode, f"譯 {block.index}") for block in _BLOCKS
    ]
    for chunk in _CHUNKS:
        stored = read_model(
            layout.chunk_translation(*chunk.index_range), ChunkTranslation
        )
        assert stored == _translated(chunk)
    assert [task.name for task in agents.tasks] == [
        "chunks/0001-0002",
        "chunks/0003-0004",
        "chunks/0005-0006",
    ]


def test_each_chunk_gets_its_own_dirs_window_and_media(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    for chunk, task in zip(_CHUNKS, agents.tasks, strict=True):
        chunk_dir = layout.chunk_dir(*chunk.index_range)
        assert task.workdir == chunk_dir
        assert task.session_dir == chunk_dir / "session"
        assert task.attempts == 2
        *frames, audio = task.media
        assert audio == Media(MediaKind.AUDIO, layout.chunk_audio(*chunk.index_range))
        assert audio.path.is_file()
        assert frames
        assert all(frame.kind is MediaKind.IMAGE for frame in frames)
        assert all(frame.path.parent == chunk_dir / "frames" for frame in frames)
        assert task.tools is not None
        assert task.tools.frames is not None
        span = chunk.time_range
        assert task.tools.frames.window == (span.start, span.end)
        assert task.tools.frames.frames_dir == chunk_dir / "frames"
    assert '"segment_summary": "seg 3-4"' in agents.tasks[1].prompt
    assert agents.tasks[1].prompt.startswith("你是第 2/3 塊翻譯員")


def test_cached_translation_skips_the_agent_and_media(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    first = _CHUNKS[0]
    write_model(
        layout.chunk_translation(*first.index_range), _translated(first, "快取")
    )

    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    assert [task.name for task in agents.tasks] == [
        "chunks/0003-0004",
        "chunks/0005-0006",
    ]
    first_dir = str(layout.chunk_dir(*first.index_range))
    assert not [argv for argv in fake_ffmpeg.calls if first_dir in argv[-1]]
    merged = read_srt_file(layout.merged_srt)
    assert [block.text for block in merged[:3]] == ["快取 1", "快取 2", "譯 3"]


def _chunk_progress(sink: RecordingSink) -> list[Event]:
    return [
        event
        for event in sink.events
        if isinstance(event, ProgressStarted | ProgressAdvanced | ProgressFinished)
        and event.scope == CHUNK_PROGRESS_SCOPE
    ]


def test_progress_counts_every_chunk_with_cache_hits_first(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
    recording_sink: RecordingSink,
) -> None:
    first = _CHUNKS[0]
    write_model(layout.chunk_translation(*first.index_range), _translated(first))
    advances_seen: list[int] = []

    def counting(chunk: Chunk) -> Callable[[AgentTask[Any]], object]:
        def run(_task: AgentTask[Any]) -> object:
            advances_seen.append(
                sum(isinstance(e, ProgressAdvanced) for e in recording_sink.events)
            )
            return _translated(chunk)

        return run

    for chunk in _CHUNKS[1:]:
        agents.script[task_name(chunk)] = counting(chunk)

    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    # The cache hit is counted before any agent runs.
    assert len(advances_seen) == 2
    assert min(advances_seen) >= 1
    assert _chunk_progress(recording_sink) == [
        ProgressStarted("chunks", "chunks", 3),
        ProgressAdvanced("chunks", 1, None),
        ProgressAdvanced("chunks", 1, None),
        ProgressAdvanced("chunks", 1, None),
        ProgressFinished("chunks"),
    ]


def test_progress_stays_open_when_the_batch_fails(
    make_context: MakeContext,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
    recording_sink: RecordingSink,
) -> None:
    agents.script["chunks/0003-0004"] = AgentOutputError("still invalid")

    with pytest.raises(TranslateError):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))

    progress = _chunk_progress(recording_sink)
    assert progress[0] == ProgressStarted("chunks", "chunks", 3)
    # No finish: a finished bar reads as every chunk done.
    assert progress[1:] == [
        ProgressAdvanced("chunks", 1, None),
        ProgressAdvanced("chunks", 1, None),
    ]


def test_stale_cache_fails_loudly(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    write_model(
        layout.chunk_translation(1, 2),
        ChunkTranslation(blocks=[ChunkLine(index=1, text="一")]),
    )

    with pytest.raises(TranslateError, match="grill reset"):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))

    assert agents.tasks == []


def test_failures_surface_after_the_batch(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    agents.script["chunks/0003-0004"] = AgentOutputError("still invalid")

    with pytest.raises(TranslateError, match="1/3 chunks failed") as caught:
        chunks.STAGE.run(make_context(StageKey.CHUNKS))

    assert "chunks/0003-0004: still invalid" in str(caught.value)
    # Every chunk ran; the ones that succeeded keep their caches.
    assert len(agents.tasks) == 3
    assert layout.chunk_translation(1, 2).exists()
    assert not layout.chunk_translation(3, 4).exists()
    assert layout.chunk_translation(5, 6).exists()
    assert not layout.merged_srt.exists()


def test_a_finished_chunk_is_cached_before_a_later_crash(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    agents.script["chunks/0003-0004"] = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))

    stored = read_model(layout.chunk_translation(1, 2), ChunkTranslation)
    assert stored == _translated(_CHUNKS[0])
    assert not layout.chunk_translation(3, 4).exists()
    assert not layout.merged_srt.exists()


def test_any_exception_fails_only_its_chunk(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    agents.script["chunks/0001-0002"] = RuntimeError("adapter bug")

    with pytest.raises(TranslateError, match="chunks/0001-0002: adapter bug"):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))

    assert layout.chunk_translation(3, 4).exists()
    assert layout.chunk_translation(5, 6).exists()


def test_a_failing_media_prep_fails_only_its_chunk(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    with pytest.raises(TranslateError, match="1/3 chunks failed") as caught:
        chunks.STAGE.run(make_context(StageKey.CHUNKS, ffmpeg=AudioFailingFfmpeg()))

    assert "chunks/0003-0004: audio slice failed" in str(caught.value)
    assert [task.name for task in agents.tasks] == [
        "chunks/0001-0002",
        "chunks/0005-0006",
    ]
    assert layout.chunk_translation(1, 2).exists()
    assert layout.chunk_translation(5, 6).exists()


def test_missing_upstream_video_names_the_reset(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
) -> None:
    layout.video.unlink()

    with pytest.raises(MissingArtifactError, match="--from combine"):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))


def test_invalid_output_goes_through_the_validator(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    chunk = _CHUNKS[0]
    incomplete = ChunkTranslation(blocks=[ChunkLine(index=1, text="一")])
    agents.script[task_name(chunk)] = Rounds(incomplete, _translated(chunk))

    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    stored = read_model(layout.chunk_translation(*chunk.index_range), ChunkTranslation)
    assert stored == _translated(chunk)


@pytest.mark.parametrize(
    "config_data",
    [{"roles": {"chunk": "codex/gpt-5.5/high"}}],
    indirect=True,
)
def test_backend_without_audio_input_gets_no_slices(
    make_context: MakeContext, agents: FakeAgentRunner, fake_ffmpeg: FakeFfmpeg
) -> None:
    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    assert all(
        item.kind is MediaKind.IMAGE for task in agents.tasks for item in task.media
    )
    assert not [argv for argv in fake_ffmpeg.calls if "libopus" in argv]
    assert "no audio is available" in agents.tasks[0].instructions


def test_reads_the_effective_briefing_and_official_cc(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    write_model(
        layout.glossary_briefing,
        make_briefing(*(c.index_range for c in _CHUNKS), summary="checked"),
    )
    write_srt_file(
        layout.ja_official_srt, [SrtBlock(1, "00:00:02,000 --> 00:00:03,000", "公式")]
    )

    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    first, *rest = agents.tasks
    assert '"summary": "checked"' in first.prompt
    assert "公式" in first.prompt
    assert all("公式" not in task.prompt for task in rest)


def test_chunks_read_the_voiced_source_and_the_blocks_before_them(
    make_context: MakeContext, agents: FakeAgentRunner
) -> None:
    chunks.STAGE.run(make_context(StageKey.CHUNKS))

    prompts = {task.name: task.prompt for task in agents.tasks}
    first = prompts[task_name(_CHUNKS[0])]
    third = prompts[task_name(_CHUNKS[2])]
    assert "【前文" not in first
    assert "[V1] line 1" in first
    # Fewer blocks precede chunk 5-6 than the context size: all of them show.
    preceding = third[third.index("【前文") : third.index("【SRT 區段")]
    assert all(f"[V{index}] line {index}" in preceding for index in range(1, 5))
    assert "[V5] line 5" in third[third.index("【SRT 區段") :]


def test_missing_voiced_source_names_the_transcript_stage(
    make_context: MakeContext, layout: ProjectLayout
) -> None:
    layout.ja_voiced_srt.unlink()

    with pytest.raises(MissingArtifactError, match="transcript"):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))


def test_voiced_source_that_diverges_from_ja_srt_fails_loudly(
    make_context: MakeContext, layout: ProjectLayout
) -> None:
    stale = _voiced(_BLOCKS)
    stale[2] = replace(stale[2], text="[V3] old wording")
    write_srt_file(layout.ja_voiced_srt, stale)

    with pytest.raises(TranslateError, match="--only transcript"):
        chunks.STAGE.run(make_context(StageKey.CHUNKS))
