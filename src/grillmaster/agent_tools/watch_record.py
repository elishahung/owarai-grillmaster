"""`watch_clip`'s billed requests, as records the proofread stage prices.

Every billed request leaves one record in its span's directory: the speech
as `speech.json` (`SpeechRecord`, MAI-Transcribe-2, priced by audio length),
Muse's captions and scene as `watch.json` (`WatchRecord`), or a Muse request
billed without a usable answer as `failed-<n>.json` (`FailedWatch`), so its
spend is counted too. The first two are also the span's cache. Kept apart
from `watch_clip.py` so the grill process prices the spans without importing
the tool server's MCP stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.agent_tools.mai import Phrase
from grillmaster.agent_tools.meta import Usage
from grillmaster.core.json_artifact import read_model
from grillmaster.core.models import FrozenModel
from grillmaster.core.tool_session import MuseEffort, MuseModel

if TYPE_CHECKING:
    from pathlib import Path

SPEECH_FILE = "speech.json"
RECORD_FILE = "watch.json"
FAILED_PREFIX = "failed-"


class BilledRequest(FrozenModel):
    cost_usd: float


class SpeechRecord(BilledRequest):
    """One span's speech as MAI-Transcribe-2 heard it, its phrases timed in
    video seconds (`speaker` numbers are per span)."""

    model: str
    audio_s: float
    phrases: list[Phrase]


class MuseRequest(BilledRequest):
    model: MuseModel
    effort: MuseEffort
    usage: Usage


class WatchRecord(MuseRequest):
    """One span's captions and scene, times already shifted to the video's."""

    text: str


class FailedWatch(MuseRequest):
    """A billed Muse request that ended without an answer."""

    error: str


def failed_path(clip_dir: Path) -> Path:
    """The next free `failed-<n>.json` in `clip_dir`."""
    count = len(list(clip_dir.glob(f"{FAILED_PREFIX}*.json")))
    return clip_dir / f"{FAILED_PREFIX}{count + 1}.json"


def billed_records(clips_dir: Path) -> frozenset[str]:
    """Every billed request's record under `clips_dir`, as `<span>/<file>`."""
    paths = [
        path
        for pattern in (SPEECH_FILE, RECORD_FILE, f"{FAILED_PREFIX}*.json")
        for path in clips_dir.glob(f"*/{pattern}")
    ]
    return frozenset(path.relative_to(clips_dir).as_posix() for path in paths)


def cost_since(clips_dir: Path, seen: frozenset[str]) -> tuple[int, float]:
    """How many requests were billed under `clips_dir` beyond `seen`, and
    their USD cost."""
    new = sorted(billed_records(clips_dir) - seen)
    cost = 0.0
    for name in new:
        model = _RECORD_MODELS.get(name.rsplit("/", 1)[-1], FailedWatch)
        cost += read_model(clips_dir / name, model).cost_usd
    return len(new), cost


# The model of each record file; anything else is a `failed-<n>.json`.
_RECORD_MODELS: dict[str, type[BilledRequest]] = {
    SPEECH_FILE: SpeechRecord,
    RECORD_FILE: WatchRecord,
}
