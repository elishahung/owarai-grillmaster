from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from grillmaster.glossary.errors import GlossaryError
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    FixedGlossary,
    GlossaryEntry,
    format_fixed_glossary_block,
    load_fixed_glossary,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SAMPLE: dict[str, Any] = {
    "talents": [
        {
            "group": {"jp": ["見取り図"], "zh": "示意圖"},
            "members": [
                {"jp": ["盛山晋太郎"], "zh": "盛山晉太郎"},
                {"jp": ["リリー"], "zh": "Lily"},
            ],
        },
        {"members": [{"jp": ["みなみかわ"], "zh": "南川"}]},
    ],
    "others": [{"jp": ["M-1", "M-1グランプリ"], "zh": "M-1"}],
}


def dump(data: object) -> str:
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def write_glossary(tmp_path: Path) -> Callable[[str], Path]:
    def write(text: str) -> Path:
        path = tmp_path / "fixed_glossary.json"
        path.write_text(text, encoding="utf-8")
        return path

    return write


def test_bundled_glossary_loads():
    glossary = load_fixed_glossary()
    assert glossary.talents
    assert glossary.others
    assert FIXED_GLOSSARY_PATH.is_file()
    assert FIXED_GLOSSARY_GUIDE_PATH.is_file()


def test_entries_follow_render_order(write_glossary: Callable[[str], Path]):
    glossary = load_fixed_glossary(write_glossary(dump(SAMPLE)))
    assert [entry.zh for entry in glossary.entries()] == [
        "示意圖",
        "盛山晉太郎",
        "Lily",
        "南川",
        "M-1",
    ]
    assert glossary.talents[1].group is None


def test_prompt_block(write_glossary: Callable[[str], Path]):
    glossary = load_fixed_glossary(write_glossary(dump(SAMPLE)))
    assert format_fixed_glossary_block(glossary) == (
        "\n【固定詞彙表（完整參照表；僅在該名稱實際出現時才套用，容許 ASR 誤聽，未出現者忽略）】\n"
        "〔藝人/組合〕\n"
        "・組合：見取り図 → 示意圖\n"
        "    · 盛山晋太郎 → 盛山晉太郎\n"
        "    · リリー → Lily\n"
        "・（單人）\n"
        "    · みなみかわ → 南川\n"
        "〔節目/單元/品牌/術語〕\n"
        "- M-1 / M-1グランプリ → M-1"
    )


def test_prompt_block_omits_empty_sections():
    glossary = FixedGlossary(
        others=(GlossaryEntry(jp=("吉本",), zh="吉本"),),
    )
    assert format_fixed_glossary_block(glossary).endswith(
        "】\n〔節目/單元/品牌/術語〕\n- 吉本 → 吉本"
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("{", id="not-json"),
        pytest.param(dump([]), id="not-an-object"),
        pytest.param(dump({"talents": {}}), id="talents-not-a-list"),
        pytest.param(dump({"others": [{"jp": [], "zh": "空"}]}), id="no-aliases"),
        pytest.param(dump({"others": [{"jp": [""], "zh": "空"}]}), id="empty-alias"),
        pytest.param(dump({"others": [{"jp": ["空"], "zh": ""}]}), id="empty-target"),
        pytest.param(dump({"talents": [{"members": []}]}), id="no-members"),
        pytest.param(
            dump({"others": [{"jp": ["空"], "zh": "空", "note": "x"}]}),
            id="unknown-key",
        ),
        pytest.param(dump({"extra": []}), id="unknown-section"),
    ],
)
def test_malformed_glossary_fails(write_glossary: Callable[[str], Path], text: str):
    with pytest.raises(GlossaryError, match=r"fixed_glossary\.json is invalid"):
        load_fixed_glossary(write_glossary(text))


def test_missing_file_fails(tmp_path: Path):
    with pytest.raises(GlossaryError, match="Cannot read"):
        load_fixed_glossary(tmp_path / "missing.json")
