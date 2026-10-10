from __future__ import annotations

from pathlib import Path

import pytest
from tests.fakes import FakeAgentRunner, draws

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentQuotaError
from grillmaster.agents.task import FilesOutput
from grillmaster.core.model_spec import Role
from grillmaster.extras.cover import (
    COVER_NAME,
    POSTER_NAME,
    TASK_NAME,
    build_cover_task,
    copy_cover,
    generate_cover,
)
from grillmaster.extras.errors import ExtrasError


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path / "work" / "side" / "cover"


@pytest.fixture
def poster(tmp_path: Path) -> Path:
    path = tmp_path / "poster.jpg"
    path.write_bytes(b"jpeg")
    return path


@pytest.fixture
def cover(tmp_path: Path) -> Path:
    return tmp_path / "cover.png"


def run(agents: FakeAgentRunner, *, poster: Path, cover: Path, workdir: Path):
    return generate_cover(
        agents,
        poster=poster,
        cover=cover,
        workdir=workdir,
        session_dir=workdir / "session",
    )


def test_task_needs_image_generation_and_reads_the_staged_poster(
    workdir: Path,
) -> None:
    task = build_cover_task(workdir=workdir, session_dir=workdir / "session")

    assert task.name == TASK_NAME
    assert task.role is Role.IMAGE
    assert task.requires == frozenset({Capability.IMAGE_GENERATION})
    assert task.workdir == workdir
    assert task.images == (workdir / POSTER_NAME,)
    assert task.output == FilesOutput((Path(COVER_NAME),))
    assert "`cover.png`" in task.instructions
    assert "poster.cover.png" not in task.instructions
    assert task.prompt == ""


def test_draws_the_cover_and_moves_it_into_place(
    poster: Path, cover: Path, workdir: Path
) -> None:
    agents = FakeAgentRunner({TASK_NAME: draws})

    run(agents, poster=poster, cover=cover, workdir=workdir)

    assert cover.read_bytes() == b"png"
    assert not (workdir / COVER_NAME).exists()
    # The poster is staged under the name the prompt uses.
    assert (workdir / POSTER_NAME).read_bytes() == b"jpeg"
    assert agents.task(TASK_NAME).session_dir == workdir / "session"


def test_existing_cover_skips_the_agent(
    poster: Path, cover: Path, workdir: Path
) -> None:
    cover.write_bytes(b"kept")
    agents = FakeAgentRunner()

    run(agents, poster=poster, cover=cover, workdir=workdir)

    assert agents.tasks == []
    assert cover.read_bytes() == b"kept"


def test_missing_poster_fails_before_any_agent_call(
    tmp_path: Path, cover: Path, workdir: Path
) -> None:
    agents = FakeAgentRunner()

    with pytest.raises(ExtrasError, match="Poster missing"):
        run(agents, poster=tmp_path / "poster.jpg", cover=cover, workdir=workdir)

    assert agents.tasks == []


def test_agent_errors_leave_no_cover(poster: Path, cover: Path, workdir: Path) -> None:
    agents = FakeAgentRunner({TASK_NAME: AgentQuotaError("429")})

    with pytest.raises(AgentQuotaError):
        run(agents, poster=poster, cover=cover, workdir=workdir)

    assert not cover.exists()


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"cover.png": b"png", "poster.jpg": b"jpg"}, ("cover.png", b"png")),
        ({"cover.png": b"", "poster.jpg": b"jpg"}, ("cover.jpg", b"jpg")),
        ({"poster.jpg": b"jpg"}, ("cover.jpg", b"jpg")),
    ],
)
def test_copy_cover_prefers_a_non_empty_generated_cover(
    tmp_path: Path, files: dict[str, bytes], expected: tuple[str, bytes]
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    for name, data in files.items():
        (source / name).write_bytes(data)
    target = tmp_path / "package"
    target.mkdir()

    copied = copy_cover(
        cover=source / "cover.png", poster=source / "poster.jpg", target_dir=target
    )

    name, data = expected
    assert copied is not None
    assert copied == target / name
    assert copied.read_bytes() == data
    assert [path.name for path in target.iterdir()] == [name]


def test_copy_cover_without_images_copies_nothing(tmp_path: Path) -> None:
    copied = copy_cover(
        cover=tmp_path / "cover.png",
        poster=tmp_path / "poster.jpg",
        target_dir=tmp_path,
    )

    assert copied is None
