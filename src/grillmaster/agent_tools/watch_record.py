"""`watch_clip`'s billed requests, as records the proofread stage prices.

Every request Meta bills leaves one record in its span's directory: the
answer as `watch.json` (`WatchRecord`, also the span's cache), or a request
that was billed without a usable answer as `failed-<n>.json`
(`FailedWatch`), so its spend is counted too. Kept apart from
`watch_clip.py` so the grill process prices the spans without importing the
tool server's MCP stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.agent_tools.meta import Usage
from grillmaster.core.json_artifact import read_model
from grillmaster.core.models import FrozenModel
from grillmaster.core.tool_session import MuseEffort, MuseModel

if TYPE_CHECKING:
    from pathlib import Path

RECORD_FILE = "watch.json"
FAILED_PREFIX = "failed-"


class BilledRequest(FrozenModel):
    model: MuseModel
    effort: MuseEffort
    usage: Usage
    cost_usd: float


class WatchRecord(BilledRequest):
    """One span's answer, times already shifted to the video's."""

    text: str


class FailedWatch(BilledRequest):
    """A billed request that ended without an answer."""

    error: str


def failed_path(clip_dir: Path) -> Path:
    """The next free `failed-<n>.json` in `clip_dir`."""
    count = len(list(clip_dir.glob(f"{FAILED_PREFIX}*.json")))
    return clip_dir / f"{FAILED_PREFIX}{count + 1}.json"


def billed_records(clips_dir: Path) -> frozenset[str]:
    """Every billed request's record under `clips_dir`, as `<span>/<file>`."""
    paths = [
        *clips_dir.glob(f"*/{RECORD_FILE}"),
        *clips_dir.glob(f"*/{FAILED_PREFIX}*.json"),
    ]
    return frozenset(path.relative_to(clips_dir).as_posix() for path in paths)


def cost_since(clips_dir: Path, seen: frozenset[str]) -> tuple[int, float]:
    """How many requests were billed under `clips_dir` beyond `seen`, and
    their USD cost."""
    new = sorted(billed_records(clips_dir) - seen)
    cost = 0.0
    for name in new:
        model = WatchRecord if name.endswith(f"/{RECORD_FILE}") else FailedWatch
        cost += read_model(clips_dir / name, model).cost_usd
    return len(new), cost
