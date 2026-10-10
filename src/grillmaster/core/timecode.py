"""SRT/ASS timecode parsing and formatting: the only copy in the codebase."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TIMECODE_LINE_RE = re.compile(
    r"^\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$"
)
_MINUTE = 60


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A span on a media timeline, in seconds."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlaps(self, other: TimeRange) -> bool:
        return self.end > other.start and self.start < other.end

    def padded(self, seconds: float) -> TimeRange:
        return TimeRange(self.start - seconds, self.end + seconds)


def parse_timecode_ms(line: str) -> tuple[int, int]:
    """Parse an SRT timecode line into integer start/end milliseconds."""
    match = _TIMECODE_LINE_RE.match(line)
    if match is None:
        raise ValueError(f"Invalid SRT timecode line: {line!r}")
    sh, sm, ss, sms, eh, em, es, ems = (int(group) for group in match.groups())
    start = ((sh * 60 + sm) * 60 + ss) * 1000 + sms
    end = ((eh * 60 + em) * 60 + es) * 1000 + ems
    return start, end


def parse_timecode_line(line: str) -> TimeRange:
    """Parse an SRT timecode line into a `TimeRange` in seconds."""
    start_ms, end_ms = parse_timecode_ms(line)
    return TimeRange(start_ms / 1000, end_ms / 1000)


def format_srt_time(seconds: float) -> str:
    """Format seconds as an SRT time `HH:MM:SS,mmm`."""
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_timecode_line(start: float, end: float) -> str:
    """Format an SRT timecode line `start --> end`."""
    return f"{format_srt_time(start)} --> {format_srt_time(end)}"


def format_ass_centiseconds(centiseconds: int) -> str:
    """Format centiseconds as an ASS time `H:MM:SS.cc` (single-digit hour)."""
    hours, rest = divmod(max(0, centiseconds), 360_000)
    minutes, rest = divmod(rest, 6_000)
    seconds, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def ass_time_from_ms(milliseconds: int) -> str:
    """ASS time for an SRT millisecond value, truncating like Aegisub does."""
    return format_ass_centiseconds(milliseconds // 10)


def ass_centiseconds(seconds: float) -> int:
    """Round a float time to ASS centiseconds (used for computed event times)."""
    return max(0, round(seconds * 100))


def format_clock(seconds: float) -> str:
    """Compact human clock: `MM:SS`, or `H:MM:SS` past an hour."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_elapsed(seconds: float) -> str:
    """Elapsed duration for logs: `12.34s`, `3m 4.5s`, `1h 2m 3.4s`."""
    if round(seconds, 2) < _MINUTE:
        return f"{seconds:.2f}s"
    # Split whole tenths so rounding never prints a `60.0s` remainder.
    minutes, tenths = divmod(round(seconds * 10), _MINUTE * 10)
    if minutes < _MINUTE:
        return f"{minutes}m {tenths / 10:.1f}s"
    hours, minutes = divmod(minutes, _MINUTE)
    return f"{hours}h {minutes}m {tenths / 10:.1f}s"
