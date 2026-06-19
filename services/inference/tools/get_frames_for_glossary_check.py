"""CLI wrapper for glossary-check on-demand frame extraction."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.inference.tools.get_frames import (  # noqa: E402
    FrameToolStage,
    main_for_stage,
)


if __name__ == "__main__":
    raise SystemExit(main_for_stage(FrameToolStage.GLOSSARY_CHECK))
