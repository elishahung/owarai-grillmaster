from __future__ import annotations

import os
from pathlib import Path

import pytest

from grillmaster.core.paths import (
    MAX_COMPONENT_UNITS,
    attempt_path,
    component_units,
    fit_dir_name,
    measure,
)

KEEP = "260503_ep123"


def _fit(*, parent: str, tail: str, max_path_units: int, reserve: int = 20) -> str:
    return fit_dir_name(
        parent=Path(parent),
        keep=KEEP,
        tail=tail,
        reserve=reserve,
        max_path_units=max_path_units,
    )


def _abs_units(parent: str) -> int:
    return measure(os.path.abspath(parent))  # noqa: PTH100


def test_short_name_is_returned_unchanged():
    assert (
        _fit(parent="C:/archive/26/05", tail="demo_show", max_path_units=259)
        == "260503_ep123_demo_show"
    )


def test_long_name_is_trimmed_to_the_remaining_budget():
    parent = "C:/archive/26/05"
    fitted = _fit(parent=parent, tail="t" * 200, max_path_units=_abs_units(parent) + 80)
    assert fitted.startswith("260503_ep123_t")
    assert _abs_units(parent) + 1 + measure(fitted) + 20 == _abs_units(parent) + 80


def test_component_limit_applies_even_with_a_short_parent():
    fitted = _fit(parent="C:/a", tail="t" * 400, max_path_units=4096)
    assert len(fitted) == MAX_COMPONENT_UNITS


def test_trailing_separator_characters_are_stripped():
    # The budget cuts mid-name right after an underscore; the result must not
    # look like a dangling fragment.
    parent = "C:/archive/26/05"
    budget = len(KEEP) + 1 + 51  # room for the a's plus the first underscore
    fitted = _fit(
        parent=parent,
        tail="a" * 50 + "___" + "b" * 50,
        max_path_units=_abs_units(parent) + 1 + budget + 20,
    )
    assert fitted == f"{KEEP}_{'a' * 50}"


@pytest.mark.parametrize(
    ("parent", "tail", "max_path_units"),
    [
        ("C:/deep", "t" * 200, _abs_units("C:/deep") + 1 + len(KEEP) + 20),
        ("C:/very/deep/archive/root/26/05", "demo", 30),
        ("C:/archive", "", 259),
    ],
    ids=["nothing-else-fits", "parent-too-deep", "empty-tail"],
)
def test_only_the_identity_prefix_is_kept(parent: str, tail: str, max_path_units: int):
    assert _fit(parent=parent, tail=tail, max_path_units=max_path_units) == KEEP


def test_cjk_title_is_trimmed_within_the_component_limit():
    # A Japanese title measures 1 UTF-16 unit/char but 3 UTF-8 bytes; an SMB
    # share backed by ext4 counts bytes even from Windows, so the cap is held
    # in bytes on every platform.
    fitted = _fit(parent="C:/a", tail="お笑い芸人" * 40, max_path_units=4096)
    assert component_units(fitted) <= MAX_COMPONENT_UNITS
    assert component_units(fitted) > MAX_COMPONENT_UNITS - 3
    assert fitted.startswith(f"{KEEP}_お笑い")


def test_suffix_counts_against_the_component_limit():
    fitted = fit_dir_name(
        parent=Path("C:/a"),
        keep=KEEP,
        tail="お笑い芸人" * 40,
        reserve=0,
        suffix=".partial",
        max_path_units=4096,
    )
    assert component_units(f"{fitted}.partial") <= MAX_COMPONENT_UNITS
    assert component_units(f"{fitted}.partial") > MAX_COMPONENT_UNITS - 3


def test_suffix_counts_against_the_path_limit():
    parent = "C:/archive/26/05"
    fitted = fit_dir_name(
        parent=Path(parent),
        keep=KEEP,
        tail="t" * 200,
        reserve=20,
        suffix=".partial",
        max_path_units=_abs_units(parent) + 80,
    )
    assert _abs_units(parent) + 1 + measure(f"{fitted}.partial") + 20 == (
        _abs_units(parent) + 80
    )


def test_measure_counts_platform_units():
    expected = 2 if os.name == "nt" else 4  # surrogate pair vs. UTF-8 bytes
    assert measure("😀") == expected


def test_attempts_after_the_first_are_numbered_siblings():
    first = Path("work/09_chunks/session")
    assert attempt_path(first, 1) == first
    assert attempt_path(first, 3) == Path("work/09_chunks/session.3")


def test_attempts_are_one_based():
    with pytest.raises(ValueError, match="1-based"):
        attempt_path(Path("session"), 0)
