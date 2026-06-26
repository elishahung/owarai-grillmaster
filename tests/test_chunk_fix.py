import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.translate.chunk.structural_fix as chunk_fix
from services.translate.chunk.structural_fix import ChunkFixError

_SOURCE_SRT = "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n"
_BROKEN_SRT = "7\n99:99:99,999 --> 99:99:99,999\n你好\n"
_FIXED_SRT = "1\n00:00:01,000 --> 00:00:02,000\n你好\n"


class FixChunkStructureTests(unittest.TestCase):
    def test_returns_agent_produced_fixed_srt(self):
        def _agent_writes_fixed(
            prompt, cwd, *, backend, model=None, reasoning_effort=None
        ):
            (Path(cwd) / "fixed.srt").write_text(_FIXED_SRT, encoding="utf-8")
            return "done"

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "chunk_fix"
            with patch.object(
                chunk_fix, "run_inference", side_effect=_agent_writes_fixed
            ) as run_agent:
                result = asyncio.run(
                    chunk_fix.fix_chunk_structure(
                        _SOURCE_SRT, _BROKEN_SRT, "boom", workspace
                    )
                )

            run_agent.assert_called_once()
            prompt = run_agent.call_args.kwargs["prompt"]
            self.assertIn("validate_chunk.py", prompt)
            self.assertIn("source.srt fixed.srt", prompt)
            self.assertNotIn("--tolerance", prompt)
            self.assertEqual(result, _FIXED_SRT)
            # Inputs were materialized for the agent.
            self.assertTrue((workspace / "source.srt").exists())
            self.assertTrue((workspace / "broken.srt").exists())

    def test_missing_output_raises_chunk_fix_error(self):
        def _agent_writes_nothing(
            prompt, cwd, *, backend, model=None, reasoning_effort=None
        ):
            return "done"

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "chunk_fix"
            with patch.object(
                chunk_fix, "run_inference", side_effect=_agent_writes_nothing
            ):
                with self.assertRaises(ChunkFixError):
                    asyncio.run(
                        chunk_fix.fix_chunk_structure(
                            _SOURCE_SRT, _BROKEN_SRT, "boom", workspace
                        )
                    )


if __name__ == "__main__":
    unittest.main()
