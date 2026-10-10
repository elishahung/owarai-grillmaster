"""Shared SRT primitives: data type, IO, timecode helpers.

Centralizes the structural building blocks of SubRip subtitle files so that
ASR builders, translators, refiners, and ASS converters all agree on the same
on-disk shape. This module deliberately stays free of domain-specific logic
(no ASR segmentation, no translation chunking, no validation rules).
"""

from .io import parse_srt, serialize_srt
from .timecode import TIMECODE_LINE_REGEX, format_timecode
from .types import SrtBlock

__all__ = [
    "SrtBlock",
    "parse_srt",
    "serialize_srt",
    "format_timecode",
    "TIMECODE_LINE_REGEX",
]
