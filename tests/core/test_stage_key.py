from __future__ import annotations

from grillmaster.core.stage_key import StageKey


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
        "finalize",
        "chat_translate",
    ]


def test_numbers_are_execution_positions():
    assert StageKey.METADATA.number == 1
    assert StageKey.PREPASS.number == 8
    assert StageKey.CHAT_TRANSLATE.number == 13


def test_keys_are_plain_strings():
    assert StageKey("chunks") is StageKey.CHUNKS
    assert f"{StageKey.REFINE}" == "refine"
