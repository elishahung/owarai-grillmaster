from __future__ import annotations

from grillmaster.core.id_coverage import IdCoverage, id_coverage


def test_full_coverage_in_any_order_is_valid():
    coverage = id_coverage([1, 2, 3], [(3, "c"), (1, "a"), (2, "b")])
    assert coverage == IdCoverage(missing=(), unknown=(), duplicate=(), empty=())
    assert not coverage


def test_every_kind_lists_its_ids_sorted():
    coverage = id_coverage(
        [1, 2, 3, 4],
        [(2, "b"), (2, "again"), (9, "x"), (7, ""), (3, "")],
    )
    assert coverage == IdCoverage(
        missing=(1, 4), unknown=(7, 9), duplicate=(2,), empty=(3,)
    )
    assert coverage


def test_missing_ids_are_allowed_unless_required():
    assert not id_coverage([1, 2], [(1, "a")], require_all=False)
    assert id_coverage([1, 2], [(1, "a")]).missing == (2,)
