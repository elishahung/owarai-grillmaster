import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import project as project_module
import services.postprocess.glossary_check as gc
from project import Project
from services.fixed_glossary.fixed_glossary import FixedGlossary

_FAKE_GLOSSARY = FixedGlossary(
    talents=(),
    others=(
        (["ギャロップ"], "Gallop"),
        (["ロングコートダディ"], "Long Coat Daddy"),
    ),
)

_HAN_ONLY_SRT = """1
00:00:01,000 --> 00:00:02,000
這是純中文字幕

2
00:00:02,000 --> 00:00:03,000
完全沒有英文或假名
"""

_KANA_SRT = """1
00:00:01,000 --> 00:00:02,000
這是純中文字幕

2
00:00:02,000 --> 00:00:03,000
他在コーナー登場
"""

# A lone Latin letter and a lone kana — no >=2 consecutive run of either.
_SINGLE_CHAR_SRT = """1
00:00:01,000 --> 00:00:02,000
他拿到A獎

2
00:00:02,000 --> 00:00:03,000
這個ア沒問題
"""

_VALID_PREPASS_JSON = """{
  "summary": "summary",
  "characters": [],
  "proper_nouns": {},
  "glossary": {},
  "catchphrases": [],
  "tone_notes": "tone",
  "segment_summaries": []
}
"""


class GlossaryCheckTests(unittest.TestCase):
    def _make_project(self) -> Project:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        root = base / "tmp_glossary_check"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        patcher = patch.object(
            project_module, "PROJECT_ROOT_NAME", str(root)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        project = Project(id="demo")
        project.project_path.mkdir(parents=True, exist_ok=True)
        project.pre_pass_path.parent.mkdir(parents=True, exist_ok=True)
        project.pre_pass_path.write_text(
            _VALID_PREPASS_JSON, encoding="utf-8"
        )
        return project

    def _write_refined(self, project: Project, content: str) -> None:
        project.refined_srt_path.write_text(content, encoding="utf-8")

    def test_no_suspect_blocks_still_runs_full_review(self):
        project = self._make_project()
        self._write_refined(project, _HAN_ONLY_SRT)

        with patch.object(
            gc, "run_inference", side_effect=self._valid_codex(project)
        ) as run_codex:
            gc.glossary_check_subtitles(project)

        run_codex.assert_called_once()
        self.assertTrue(project.glossary_checked_srt_path.exists())
        self.assertTrue(project.pre_pass_raw_path.exists())

    def test_single_char_latin_or_kana_still_runs_full_review(self):
        project = self._make_project()
        self._write_refined(project, _SINGLE_CHAR_SRT)

        with patch.object(
            gc, "run_inference", side_effect=self._valid_codex(project)
        ) as run_codex:
            gc.glossary_check_subtitles(project)

        run_codex.assert_called_once()
        self.assertTrue(project.glossary_checked_srt_path.exists())

    def _valid_codex(self, project: Project):
        def _side_effect(*args, **kwargs):
            shutil.copyfile(
                project.refined_srt_path, project.glossary_checked_srt_path
            )
            return "done"

        return _side_effect

    def test_exact_glossary_zh_token_is_skipped(self):
        project = self._make_project()
        self._write_refined(
            project,
            "1\n00:00:01,000 --> 00:00:02,000\n這是純中文字幕\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n他在Gallop壓軸登場\n",
        )

        with (
            patch.object(
                gc, "load_fixed_glossary", return_value=_FAKE_GLOSSARY
            ),
            patch.object(
                gc, "run_inference", side_effect=self._valid_codex(project)
            ) as run_codex,
        ):
            gc.glossary_check_subtitles(project)

        run_codex.assert_called_once()
        self.assertTrue(project.glossary_checked_srt_path.exists())

    def test_partial_glossary_zh_token_stays_flagged(self):
        project = self._make_project()
        self._write_refined(
            project,
            "1\n00:00:01,000 --> 00:00:02,000\n這是純中文字幕\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n他喜歡Long Coat的演出\n",
        )

        with (
            patch.object(
                gc, "load_fixed_glossary", return_value=_FAKE_GLOSSARY
            ),
            patch.object(
                gc, "run_inference", side_effect=self._valid_codex(project)
            ) as run_codex,
        ):
            gc.glossary_check_subtitles(project)

        run_codex.assert_called_once()
        self.assertTrue(project.glossary_checked_srt_path.exists())

    def test_glossary_zh_embedded_in_larger_token_stays_flagged(self):
        project = self._make_project()
        self._write_refined(
            project,
            "1\n00:00:01,000 --> 00:00:02,000\n這是純中文字幕\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n他看了GallopXY節目\n",
        )

        with (
            patch.object(
                gc, "load_fixed_glossary", return_value=_FAKE_GLOSSARY
            ),
            patch.object(
                gc, "run_inference", side_effect=self._valid_codex(project)
            ) as run_codex,
        ):
            gc.glossary_check_subtitles(project)

        run_codex.assert_called_once()

    def test_existing_output_is_idempotent(self):
        project = self._make_project()
        self._write_refined(project, _KANA_SRT)
        project.glossary_checked_srt_path.write_text(
            _KANA_SRT, encoding="utf-8"
        )

        with patch.object(gc, "run_inference") as run_codex:
            gc.glossary_check_subtitles(project)

        run_codex.assert_not_called()

    def test_pre_pass_raw_is_created_once_and_not_overwritten(self):
        project = self._make_project()
        self._write_refined(project, _HAN_ONLY_SRT)
        project.pre_pass_raw_path.write_text(
            '{"summary":"older backup"}', encoding="utf-8"
        )

        with patch.object(
            gc, "run_inference", side_effect=self._valid_codex(project)
        ):
            gc.glossary_check_subtitles(project)

        self.assertEqual(
            project.pre_pass_raw_path.read_text(encoding="utf-8"),
            '{"summary":"older backup"}',
        )

    def test_invalid_updated_pre_pass_raises(self):
        project = self._make_project()
        self._write_refined(project, _HAN_ONLY_SRT)

        def _write_bad_prepass(*args, **kwargs):
            shutil.copyfile(
                project.refined_srt_path, project.glossary_checked_srt_path
            )
            project.pre_pass_path.write_text(
                '{"summary": 123}', encoding="utf-8"
            )
            project.glossary_check_report_path.write_text(
                "# report\n", encoding="utf-8"
            )
            return "done"

        with patch.object(gc, "run_inference", side_effect=_write_bad_prepass):
            with self.assertRaises(gc.GlossaryCheckError):
                gc.glossary_check_subtitles(project)

    def test_changed_srt_requires_report(self):
        project = self._make_project()
        self._write_refined(project, _HAN_ONLY_SRT)

        def _write_changed_srt(*args, **kwargs):
            project.glossary_checked_srt_path.write_text(
                _HAN_ONLY_SRT.replace("純中文字幕", "純中文台詞"),
                encoding="utf-8",
            )
            return "done"

        with patch.object(gc, "run_inference", side_effect=_write_changed_srt):
            with self.assertRaises(gc.GlossaryCheckError):
                gc.glossary_check_subtitles(project)

    def test_codex_failure_cleans_copied_glossary(self):
        project = self._make_project()
        self._write_refined(project, _KANA_SRT)

        with patch.object(
            gc, "run_inference", side_effect=RuntimeError("codex boom")
        ):
            with self.assertRaises(RuntimeError):
                gc.glossary_check_subtitles(project)

        cache = project.glossary_check_cache_dir
        self.assertFalse((cache / "fixed_glossary.json").exists())
        self.assertFalse((cache / "fixed_glossary.md").exists())

    def test_structural_divergence_raises_and_cleans(self):
        project = self._make_project()
        self._write_refined(project, _KANA_SRT)

        def _write_bad_output(*args, **kwargs):
            # One block instead of two -> structural mismatch.
            project.glossary_checked_srt_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n這是純中文字幕\n",
                encoding="utf-8",
            )
            return "done"

        with patch.object(
            gc, "run_inference", side_effect=_write_bad_output
        ):
            with self.assertRaises(gc.GlossaryCheckError):
                gc.glossary_check_subtitles(project)

        cache = project.glossary_check_cache_dir
        self.assertFalse((cache / "fixed_glossary.json").exists())
        self.assertFalse((cache / "fixed_glossary.md").exists())


if __name__ == "__main__":
    unittest.main()
