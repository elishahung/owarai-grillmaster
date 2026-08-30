import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project import Project
from services import package as package_module
from services.inference import InferenceResult
from services.package import core as package_core
from services.package import titles as package_titles


_AGENT_TITLES = {
    "titles": [
        {"title": "搭檔末日", "reason": "沿用企劃名"},
        {"title": "控比配對", "reason": "影射配對機制"},
        {"title": "職業修羅場", "reason": "點出現場張力"},
    ]
}


class PackageTitlesTests(unittest.TestCase):
    def _make_source(self, *, with_pre_pass: bool = True) -> Path:
        root = Path(tempfile.mkdtemp(prefix="package-titles-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        source = root / "source"
        source.mkdir()
        if with_pre_pass:
            (source / ".pre_pass").mkdir()
            (source / ".pre_pass" / "pre_pass.json").write_text(
                '{"summary":"demo"}', encoding="utf-8"
            )
        return source

    def _write_titles(self, source: Path, payload: str) -> Path:
        path = package_titles.titles_path(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return path

    def _enable(self, enabled: bool):
        return patch.object(
            package_titles.settings,
            "enable_package_title_suggestion",
            enabled,
        )

    def _fake_inference(self):
        return patch.object(
            package_titles,
            "run_inference",
            return_value=InferenceResult(
                text=json.dumps(_AGENT_TITLES, ensure_ascii=False),
                cost=0.0,
                requests=1,
            ),
        )

    def test_existing_titles_are_reused_without_invoking_the_agent(self):
        source = self._make_source()
        path = self._write_titles(
            source, json.dumps(_AGENT_TITLES, ensure_ascii=False)
        )

        with self._enable(True), self._fake_inference() as inference:
            result = package_titles.ensure_titles(source)

        self.assertEqual(result, path)
        inference.assert_not_called()

    def test_missing_titles_are_generated_from_pre_pass(self):
        source = self._make_source()

        with self._enable(True), self._fake_inference() as inference:
            result = package_titles.ensure_titles(source)

        self.assertEqual(result, package_titles.titles_path(source))
        saved = package_titles.load_titles(source)
        self.assertIsNotNone(saved)
        self.assertEqual(
            [item.title for item in saved.titles],
            ["搭檔末日", "控比配對", "職業修羅場"],
        )
        prompt = inference.call_args.kwargs["prompt"]
        self.assertIn('{"summary":"demo"}', prompt)
        self.assertIs(
            inference.call_args.kwargs["schema"], package_titles.TitleSuggestions
        )

    def test_disabled_setting_never_generates_titles(self):
        source = self._make_source()

        with self._enable(False), self._fake_inference() as inference:
            result = package_titles.ensure_titles(source)

        self.assertIsNone(result)
        inference.assert_not_called()
        self.assertFalse(package_titles.titles_path(source).exists())

    def test_unreadable_titles_are_regenerated(self):
        source = self._make_source()
        self._write_titles(source, "not json")

        with self._enable(True), self._fake_inference() as inference:
            package_titles.ensure_titles(source)

        inference.assert_called_once()
        saved = package_titles.load_titles(source)
        self.assertIsNotNone(saved)

    def test_generation_failure_is_swallowed(self):
        source = self._make_source(with_pre_pass=False)

        with self._enable(True), self._fake_inference() as inference:
            result = package_titles.ensure_titles(source)

        self.assertIsNone(result)
        inference.assert_not_called()

    def test_package_generates_and_copies_titles(self):
        source = self._make_source()
        package_root = source.parent / "package"
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        project = Project(id="demo", name="show")

        def create_video(**kwargs):
            kwargs["output_file"].write_text("burned", encoding="utf-8")

        with (
            self._enable(True),
            self._fake_inference(),
            patch.object(
                package_core.MediaProcessor,
                "burn_in_subtitles",
                side_effect=create_video,
            ),
        ):
            package_module.package_project(project, source, package_root)

        packaged = package_root / "demo_show" / "info.json"
        info = json.loads(packaged.read_text(encoding="utf-8"))
        # Titles lead the merged file, the pre-pass fields follow.
        self.assertEqual(list(info), ["titles", "summary"])
        self.assertEqual(info["titles"], _AGENT_TITLES["titles"])
        self.assertEqual(info["summary"], "demo")

    def test_package_without_titles_still_completes(self):
        source = self._make_source()
        package_root = source.parent / "package"
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        project = Project(id="demo", name="show")

        def create_video(**kwargs):
            kwargs["output_file"].write_text("burned", encoding="utf-8")

        with (
            self._enable(False),
            patch.object(
                package_core.MediaProcessor,
                "burn_in_subtitles",
                side_effect=create_video,
            ),
        ):
            package_module.package_project(project, source, package_root)

        target = package_root / "demo_show"
        self.assertTrue((target / "video.mp4").exists())
        self.assertFalse((target / "titles.json").exists())
        self.assertEqual(
            json.loads((target / "info.json").read_text(encoding="utf-8")),
            {"summary": "demo"},
        )


if __name__ == "__main__":
    unittest.main()
