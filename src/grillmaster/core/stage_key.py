"""Pipeline stage and side-task keys.

`StageKey` declaration order is execution order. The 1-based `number` also
numbers each stage's work directory (owned by `project.layout`), so
reordering members is a project-layout change. Side tasks run outside that
order and get unnumbered directories.
"""

from __future__ import annotations

from enum import StrEnum


class StageKey(StrEnum):
    METADATA = "metadata"
    DOWNLOAD = "download"
    COMBINE = "combine"
    CHAT_FETCH = "chat_fetch"
    AUDIO = "audio"
    ASR = "asr"
    TRANSCRIPT = "transcript"
    PREPASS = "prepass"
    CHUNKS = "chunks"
    REFINE = "refine"
    GLOSSARY = "glossary"
    FINALIZE = "finalize"
    CHAT_TRANSLATE = "chat_translate"

    @property
    def number(self) -> int:
        """1-based execution position."""
        return _NUMBERS[self]


_NUMBERS = {key: position for position, key in enumerate(StageKey, start=1)}


class SideTaskKey(StrEnum):
    COVER = "cover"
    DATE_RESEARCH = "date_research"
