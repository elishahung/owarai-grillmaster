import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.elevenlabs.asr import (
    ELEVENLABS_STT_PRICE_PER_HOUR_USD,
    ElevenLabsASR,
    calculate_transcription_cost,
)


class ElevenLabsASRTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_elevenlabs_asr"
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _run_transcription(self, response):
        root = self._make_temp_dir()
        audio_path = root / "audio.ogg"
        json_path = root / "asr.json"
        audio_path.write_bytes(b"audio")

        with (
            patch("services.elevenlabs.asr.settings.elevenlabs_api_key", "key"),
            patch("services.elevenlabs.asr.ElevenLabs") as client_cls,
        ):
            client = client_cls.return_value
            client.speech_to_text.convert.return_value = response
            service = ElevenLabsASR()
            result = service.transcribe_to_file(audio_path, json_path)

        return json_path, client.speech_to_text.convert, result

    def test_writes_raw_response_json(self):
        response = {
            "text": "こんにちは",
            "words": [],
        }

        json_path, convert, result = self._run_transcription(response)

        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), response)
        self.assertEqual(result.audio_duration_secs, 0.0)
        self.assertEqual(result.total_cost, 0.0)
        _, kwargs = convert.call_args
        self.assertEqual(kwargs["model_id"], "scribe_v2")
        self.assertEqual(kwargs["language_code"], "jpn")
        self.assertEqual(kwargs["timestamps_granularity"], "word")
        self.assertTrue(kwargs["diarize"])
        self.assertNotIn("additional_formats", kwargs)

    def test_accepts_sdk_model_dump_response(self):
        response = MagicMock()
        response.model_dump.return_value = {
            "text": "こんにちは",
            "words": [],
        }

        json_path, _, _ = self._run_transcription(response)

        self.assertEqual(
            json.loads(json_path.read_text(encoding="utf-8")),
            response.model_dump.return_value,
        )

    def test_calculates_cost_from_audio_duration_secs(self):
        result = calculate_transcription_cost({"audio_duration_secs": 7200})

        self.assertEqual(result.audio_duration_secs, 7200.0)
        self.assertEqual(result.total_cost, ELEVENLABS_STT_PRICE_PER_HOUR_USD * 2)

    def test_calculates_cost_from_latest_word_end_when_duration_missing(self):
        result = calculate_transcription_cost(
            {
                "words": [
                    {"text": "a", "start": 0.0, "end": 1.5},
                    {"text": "b", "start": 1.5, "end": 9.0},
                ],
            }
        )

        self.assertEqual(result.audio_duration_secs, 9.0)
        self.assertAlmostEqual(
            result.total_cost,
            (9.0 / 3600) * ELEVENLABS_STT_PRICE_PER_HOUR_USD,
        )


if __name__ == "__main__":
    unittest.main()
