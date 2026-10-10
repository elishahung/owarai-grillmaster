"""Re-record the live agent fixtures: `uv run python scripts/record_agent_fixture.py [agy|codex|claude]...`.

Runs the `live`-marked tests in `tests/agents/test_live.py` (spends a little
subscription quota per backend) and saves each backend's raw streams under
`tests/fixtures/agents/<backend>/live_{start,resume}.jsonl`. With no
argument every backend is recorded.
"""

from __future__ import annotations

import os
import sys

import pytest


def main(backends: list[str]) -> int:
    os.environ["GRILL_RECORD_FIXTURES"] = "1"
    args = ["-m", "live", "tests/agents/test_live.py", "-p", "no:cacheprovider"]
    if backends:
        args += ["-k", " or ".join(backends)]
    return pytest.main(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
