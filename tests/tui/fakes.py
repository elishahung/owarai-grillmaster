from __future__ import annotations

from grillmaster.events.types import PlanEntry, PlanKind


class FakeClock:
    """A settable monotonic clock."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


PLAN = (
    PlanEntry("metadata", "Fetch metadata", PlanKind.STAGE, weight=1),
    PlanEntry("download", "Download video", PlanKind.STAGE, weight=4),
    PlanEntry(
        "chunks",
        "Translate chunks",
        PlanKind.STAGE,
        params={"model": "agy/gemini-3.1-pro"},
        weight=12,
    ),
    PlanEntry("refine", "Refine subtitles", PlanKind.STAGE, enabled=False, weight=3),
    PlanEntry("finalize", "Finalize subtitles", PlanKind.STAGE, weight=1),
    PlanEntry("package", "Package", PlanKind.DELIVERY, weight=4),
    PlanEntry("archive", "Archive", PlanKind.DELIVERY, weight=1),
    PlanEntry("cover", "Generate cover", PlanKind.SIDE_TASK),
    PlanEntry("date_research", "Research date", PlanKind.SIDE_TASK),
)
