"""Coverage check for agent outputs keyed by an integer id.

Chunk translations (`{index, text}` per SRT block) and chat batches
(`{id, text}` per message) must both return one non-empty text per expected
id and nothing else. The check is shared; each caller words its own repair
message.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class IdCoverage:
    """The offending ids of each kind, sorted; all empty means valid."""

    missing: tuple[int, ...]
    unknown: tuple[int, ...]
    duplicate: tuple[int, ...]
    empty: tuple[int, ...]

    def __bool__(self) -> bool:
        """Whether any id is wrong."""
        return bool(self.missing or self.unknown or self.duplicate or self.empty)


def id_coverage(
    expected: Iterable[int],
    returned: Iterable[tuple[int, str]],
    *,
    require_all: bool = True,
) -> IdCoverage:
    """Missing (when `require_all`), unknown and duplicate ids, and expected
    ids whose text is empty.

    `returned` holds `(id, text)` pairs with each text already reduced to
    what will be used, so the caller decides what counts as empty.
    """
    expected_set = set(expected)
    pairs = list(returned)
    counts = Counter(item_id for item_id, _ in pairs)
    return IdCoverage(
        missing=tuple(sorted(expected_set - counts.keys())) if require_all else (),
        unknown=tuple(sorted(counts.keys() - expected_set)),
        duplicate=tuple(sorted(i for i, count in counts.items() if count > 1)),
        empty=tuple(sorted({i for i, text in pairs if i in expected_set and not text})),
    )
