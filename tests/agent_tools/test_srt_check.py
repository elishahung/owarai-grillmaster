from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from grillmaster.agent_tools.srt_check import VALID, SrtChecker
from grillmaster.core.srt import serialize_srt, write_srt_file

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.tool_session import SrtCheckTool


@pytest.fixture
def checker(srt_config: SrtCheckTool) -> SrtChecker:
    return SrtChecker(srt_config)


def test_rewritten_candidate_is_valid(
    checker: SrtChecker, tmp_path: Path, reference_blocks: list[SrtBlock]
):
    write_srt_file(
        tmp_path / "work" / "10_refine" / "refined.srt",
        [replace(block, text="改寫") for block in reference_blocks],
    )
    assert (
        checker.check_srt(str(tmp_path / "work" / "10_refine" / "refined.srt")) == VALID
    )


def test_bom_is_accepted(
    checker: SrtChecker, tmp_path: Path, reference_blocks: list[SrtBlock]
):
    candidate = tmp_path / "elsewhere" / "out.srt"
    candidate.parent.mkdir()
    candidate.write_text(serialize_srt(reference_blocks), encoding="utf-8-sig")
    assert checker.check_srt(str(candidate)) == VALID


def test_structural_problems_are_listed(
    checker: SrtChecker, tmp_path: Path, reference_blocks: list[SrtBlock]
):
    write_srt_file(
        tmp_path / "out.srt",
        [reference_blocks[0], replace(reference_blocks[1], text="")],
    )
    assert checker.check_srt(str(tmp_path / "out.srt")) == (
        "INVALID\n"
        "- block count differs: reference=3 candidate=2\n"
        "- block 2: text is empty"
    )


def test_missing_candidate_is_a_problem(checker: SrtChecker, tmp_path: Path):
    result = checker.check_srt(str(tmp_path / "nope.srt"))
    assert result == f"INVALID\n- file not found: {tmp_path / 'nope.srt'}"


def test_malformed_candidate_is_a_problem(checker: SrtChecker, tmp_path: Path):
    (tmp_path / "bad.srt").write_text("1\nnot a timecode\ntext\n", encoding="utf-8")
    result = checker.check_srt(str(tmp_path / "bad.srt"))
    assert result.startswith(f"INVALID\n- cannot parse {tmp_path / 'bad.srt'}: ")
    assert "Invalid SRT timecode line" in result


def test_reference_is_parsed_once_when_built(
    checker: SrtChecker, srt_config: SrtCheckTool, tmp_path: Path
):
    reference = srt_config.reference_srt
    candidate = tmp_path / "out.srt"
    reference.replace(candidate)  # the reference file is gone after building
    assert checker.check_srt(str(tmp_path / "out.srt")) == VALID


def test_relative_paths_are_rejected(checker: SrtChecker):
    assert checker.check_srt("refined.srt") == (
        "INVALID\n- pass an absolute path, got: refined.srt"
    )


def test_missing_reference_is_a_session_error(srt_config: SrtCheckTool):
    srt_config.reference_srt.unlink()
    with pytest.raises(FileNotFoundError):
        SrtChecker(srt_config)
