from __future__ import annotations

from grillmaster.core.stage_key import SideTaskKey, StageKey


def test_declaration_order_is_execution_order():
    assert [key.value for key in StageKey] == [
        "metadata",
        "download",
        "combine",
        "chat_fetch",
        "audio",
        "asr",
        "transcript",
        "prepass",
        "chunks",
        "refine",
        "glossary",
        "proofread",
        "finalize",
        "chat_translate",
        "telop",
    ]


def test_numbers_are_execution_positions():
    assert StageKey.METADATA.number == 1
    assert StageKey.PREPASS.number == 8
    assert StageKey.CHAT_TRANSLATE.number == 14


def test_keys_are_plain_strings():
    assert StageKey("chunks") is StageKey.CHUNKS
    assert f"{StageKey.REFINE}" == "refine"


def test_side_task_keys():
    assert [key.value for key in SideTaskKey] == [
        "cover",
        "date_research",
        "telop_scan",
    ]
