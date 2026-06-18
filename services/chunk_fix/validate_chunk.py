"""CLI the fix agent runs to self-check a candidate chunk SRT.

Prints `VALID` and exits 0 when the candidate matches the source skeleton
within tolerance; otherwise prints the validator's error string and exits 1.

Self-contained on purpose: it bootstraps `sys.path` from its own location so
`services` is importable regardless of the agent's cwd, and it imports only
`services.srt` + `services.chunk_fix.validation` (no `settings`, no `.env`).
Tolerance is passed as a flag so this never depends on app configuration.

Usage:
    python validate_chunk.py SOURCE.srt CANDIDATE.srt --tolerance N
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root is three levels up: <repo>/services/chunk_fix/validate_chunk.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.chunk_fix.validation import validate_chunk_structure  # noqa: E402
from services.srt import parse_srt  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="authoritative source SRT")
    parser.add_argument("candidate", type=Path, help="candidate SRT to check")
    parser.add_argument(
        "--tolerance",
        type=int,
        required=True,
        help="max missing/extra source blocks allowed",
    )
    args = parser.parse_args(argv)

    source_blocks = parse_srt(args.source.read_text(encoding="utf-8-sig"))
    candidate_text = args.candidate.read_text(encoding="utf-8-sig")

    try:
        validate_chunk_structure(source_blocks, candidate_text, args.tolerance)
    except ValueError as error:
        print(str(error))
        return 1

    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
