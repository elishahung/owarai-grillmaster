import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from services.inference import InferenceResult
from services.live_chat import (
    ChatTranslationInputs,
    parse_live_chat,
    render_chat_panel,
    translate_live_chat,
)
from services.live_chat import render as chat_render
from services.live_chat import translate as chat_translate
from services.progress import NoopProgressReporter
from services.live_chat.schema import (
    ChatLog,
    ChatMessage,
    TranslatedChatLog,
    TranslatedChatMessage,
)


def _replay_line(offset_ms: int, item: dict) -> str:
    return json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": str(offset_ms),
                "actions": [{"addChatItemAction": {"item": item}}],
            }
        },
        ensure_ascii=False,
    )


def _text_item(author: str, *runs: dict) -> dict:
    return {
        "liveChatTextMessageRenderer": {
            "authorName": {"simpleText": author},
            "message": {"runs": list(runs)},
        }
    }


def _temp_dir(test: unittest.TestCase) -> Path:
    root = Path(tempfile.mkdtemp(prefix="live-chat-test-"))
    test.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
    return root


class ParseLiveChatTests(unittest.TestCase):
    def test_keeps_text_and_paid_drops_emoji_and_rebases_section(self):
        root = _temp_dir(self)
        raw = root / "live_chat.json"
        emoji = {"emoji": {"emojiId": "x", "shortcuts": [":_cookie:"]}}
        raw.write_text(
            "\n".join(
                [
                    _replay_line(5_000, _text_item("@early", {"text": "前"})),
                    _replay_line(
                        12_500,
                        _text_item("@a", {"text": "池田 "}, emoji, {"text": "ありがとう"}),
                    ),
                    _replay_line(13_000, _text_item("@emoji-only", emoji)),
                    _replay_line(
                        11_000,
                        {
                            "liveChatPaidMessageRenderer": {
                                "authorName": {"simpleText": "@donor"},
                                "purchaseAmountText": {"simpleText": "¥800"},
                            }
                        },
                    ),
                    _replay_line(
                        14_000,
                        {"liveChatMembershipItemRenderer": {"authorName": {"simpleText": "@m"}}},
                    ),
                    _replay_line(30_000, _text_item("@late", {"text": "後"})),
                ]
            ),
            encoding="utf-8",
        )

        log = parse_live_chat(raw, section_start=10.0, section_end=20.0)

        self.assertEqual(
            [(m.id, m.seconds, m.author, m.text, m.kind, m.amount) for m in log.messages],
            [
                (0, 1.0, "@donor", "", "paid", "¥800"),
                (1, 2.5, "@a", "池田 ありがとう", "text", None),
            ],
        )


class TranslateLiveChatTests(unittest.TestCase):
    def _inputs(self, root: Path, messages: list[ChatMessage]) -> ChatTranslationInputs:
        messages_path = root / ".live_chat" / "messages.json"
        messages_path.parent.mkdir(parents=True)
        messages_path.write_text(
            ChatLog(messages=messages).model_dump_json(), encoding="utf-8"
        )
        (root / "pre_pass.json").write_text('{"summary":"demo"}', encoding="utf-8")
        (root / "video.ja.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n池田です\n", encoding="utf-8"
        )
        (root / "video.cht.finalized.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n我是池田\n", encoding="utf-8"
        )
        return ChatTranslationInputs(
            messages_path=messages_path,
            pre_pass_path=root / "pre_pass.json",
            source_srt_path=root / "video.ja.srt",
            finalized_srt_path=root / "video.cht.finalized.srt",
            batches_dir=root / ".live_chat" / "batches",
            polish_path=root / ".live_chat" / "polish.json",
            output_path=root / "chat.cht.json",
        )

    def test_batches_translate_japanese_only_then_polish_applies(self):
        root = _temp_dir(self)
        messages = [
            ChatMessage(id=0, seconds=3.0, author="@a", text="池田ありがとう"),
            ChatMessage(id=1, seconds=4.0, author="@b", text="www"),
            ChatMessage(id=2, seconds=5.0, author="@c", text="すまんな池田"),
        ]
        inputs = self._inputs(root, messages)
        prompts: list[str] = []

        def fake_inference(**kwargs):
            prompts.append(kwargs["prompt"])
            if kwargs["schema"] is chat_translate.ChatPolish:
                payload = {"corrections": [{"id": 2, "text": "抱歉啦池田"}]}
            else:
                payload = {
                    "translations": [
                        {"id": 0, "text": "謝謝池田"},
                        {"id": 2, "text": "對不起池田"},
                    ]
                }
            result = InferenceResult(text=json.dumps(payload, ensure_ascii=False), cost=0.25)
            kwargs["validate"](kwargs["schema"].model_validate_json(result.text))
            return result

        costs: list[float] = []
        with patch.object(chat_translate, "run_inference", side_effect=fake_inference):
            log = translate_live_chat(inputs, on_cost=costs.append)

        self.assertEqual(
            [m.translation for m in log.messages], ["謝謝池田", "www", "抱歉啦池田"]
        )
        self.assertEqual(costs, [0.25, 0.25])
        # The briefing is compacted once and shared by batch and polish.
        self.assertIn('{"summary":"demo"}', prompts[0])
        self.assertIn('{"summary":"demo"}', prompts[1])
        # Both passes carry the glossary check's name-form rules.
        self.assertIn("## Name form", prompts[0])
        self.assertIn("## Name form", prompts[1])
        # The batch saw the finalized subtitle paired with its Japanese line,
        # and only the messages that contain Japanese script.
        self.assertIn("池田です → 我是池田", prompts[0])
        self.assertIn("0 [00:03] 池田ありがとう", prompts[0])
        self.assertNotIn("www", prompts[0].split("## Messages to translate")[1])
        saved = TranslatedChatLog.model_validate_json(
            inputs.output_path.read_text(encoding="utf-8")
        )
        self.assertEqual(saved, log)

        # Batch and polish caches make a re-run free.
        with patch.object(chat_translate, "run_inference") as run_inference:
            again = translate_live_chat(inputs, on_cost=costs.append)
        run_inference.assert_not_called()
        self.assertEqual(again, log)

    def test_batches_are_balanced_under_the_size_cap(self):
        def sizes(count: int) -> list[int]:
            log = ChatLog(
                messages=[
                    ChatMessage(id=i, seconds=float(i), author="@a", text="池田")
                    for i in range(count)
                ]
            )
            return [len(batch) for batch in chat_translate.plan_batches(log)]

        cap = chat_translate.BATCH_SIZE
        self.assertEqual(sizes(0), [])
        self.assertEqual(sizes(cap), [cap])
        self.assertEqual(sizes(cap + 34), [(cap + 34) // 2] * 2)
        self.assertEqual(sizes(2 * cap + 1), [101, 100, 100])

    def test_validation_rejects_missing_ids(self):
        root = _temp_dir(self)
        inputs = self._inputs(
            root,
            [
                ChatMessage(id=0, seconds=1.0, author="@a", text="草"),
                ChatMessage(id=1, seconds=2.0, author="@b", text="きびしい"),
            ],
        )
        captured = {}

        def fake_inference(**kwargs):
            captured["validate"] = kwargs["validate"]
            raise RuntimeError("stop")

        with patch.object(chat_translate, "run_inference", side_effect=fake_inference):
            with self.assertRaises(RuntimeError):
                translate_live_chat(inputs, on_cost=lambda cost: None)

        partial = chat_translate.ChatBatchTranslation(
            translations=[chat_translate.ChatLineTranslation(id=0, text="笑死")]
        )
        with self.assertRaisesRegex(ValueError, r"missing ids \[1\]"):
            captured["validate"](partial)


class RenderChatAssTests(unittest.TestCase):
    def _log(self) -> TranslatedChatLog:
        return TranslatedChatLog(
            messages=[
                TranslatedChatMessage(
                    id=0, seconds=1.0, author="@a", text="池田", translation="池田{\\b1}"
                ),
                TranslatedChatMessage(
                    id=1,
                    seconds=2.0,
                    author="@donor",
                    text="",
                    translation="",
                    kind="paid",
                    amount="¥800",
                ),
            ]
        )

    def test_states_scroll_older_messages_and_escape_user_text(self):
        out = io.StringIO()
        chat_render._write_document(self._log(), 5.0, out)
        ass = out.getvalue()
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]

        # Panel, then 3 events for state 1 and 3 + 4 (paid adds a highlight)
        # for state 2.
        self.assertEqual(len(dialogues), 1 + 3 + 7)
        self.assertTrue(dialogues[0].startswith("Dialogue: 0,0:00:00.00,0:00:05.00,"))
        first_state = [d for d in dialogues if ",0:00:01.00,0:00:02.00," in d]
        self.assertTrue(all("\\fad(" in d for d in first_state))
        second_state = [d for d in dialogues if ",0:00:02.00,0:00:05.00," in d]
        # The older message slides up by the newcomer's height + gap.
        older_text = next(d for d in second_state if "@a" in d)
        self.assertIn("\\move(", older_text)
        self.assertIn("¥800", next(d for d in second_state if "@donor" in d))
        # Override braces in user text are neutralized.
        self.assertIn("池田｛＼b1｝", ass)

    def test_long_text_wraps_and_truncates(self):
        lines = chat_render._wrap("あ" * 200, chat_render.BODY_FONT_SIZE, 300)
        self.assertEqual(len(lines), chat_render.MAX_BODY_LINES)
        self.assertTrue(lines[-1].endswith("…"))


class RenderChatPanelTests(unittest.TestCase):
    def test_panel_is_rendered_only_when_chat_was_translated(self):
        root = _temp_dir(self)
        video = root / "video.mp4"
        self.assertEqual(render_chat_panel(root, video), [])

        (root / "chat.cht.json").write_text(
            TranslatedChatLog(
                messages=[
                    TranslatedChatMessage(
                        id=0, seconds=1.0, author="@a", text="池田", translation="池田"
                    )
                ]
            ).model_dump_json(),
            encoding="utf-8",
        )
        with patch.object(
            chat_render.MediaProcessor, "get_media_duration", return_value=4.0
        ):
            files = render_chat_panel(root, video)

        self.assertEqual(files, [root / "video.chat.ass"])
        self.assertIn("Style: Chat,", (root / "video.chat.ass").read_text(encoding="utf-8-sig"))

    def test_unreadable_chat_is_skipped(self):
        root = _temp_dir(self)
        (root / "chat.cht.json").write_text("not json", encoding="utf-8")
        self.assertEqual(render_chat_panel(root, root / "video.mp4"), [])


class LiveChatWorkflowTests(unittest.TestCase):
    def test_chat_stages_run_after_video_and_after_finalize(self):
        from project import ProgressStage
        from workflow import api as workflow_api

        project = MagicMock()
        for stage in ProgressStage:
            setattr(project, stage.value, False)
        project.mark_progress.side_effect = lambda stage: setattr(
            project, stage.value, True
        )
        project.total_cost = 0.0
        calls: list[str] = []

        def record(name):
            return lambda *args, **kwargs: calls.append(name)

        stage_patches = {
            (workflow_api.metadata, "fetch_metadata"): "metadata",
            (workflow_api.media, "download_project_video"): "download",
            (workflow_api.media, "process_video"): "combine",
            (workflow_api.live_chat, "fetch_project_live_chat"): "chat_fetch",
            (workflow_api.media, "extract_audio"): "audio",
            (workflow_api.transcription, "run_asr"): "asr",
            (workflow_api.transcription, "convert_asr_to_srt"): "srt",
            (workflow_api.translation, "run_pre_pass"): "prepass",
            (workflow_api.translation, "translate_chunks"): "chunks",
            (workflow_api.postprocess, "finalize_project_subtitles"): "finalize",
            (workflow_api.live_chat, "translate_project_live_chat"): "chat",
        }
        with ExitStack() as stack:
            for (module, name), label in stage_patches.items():
                stack.enter_context(
                    patch.object(module, name, side_effect=record(label))
                )
            stack.enter_context(
                patch.object(
                    workflow_api.Project, "from_source_str", return_value=project
                )
            )
            stack.enter_context(patch.object(workflow_api, "SideTaskManager"))
            for setting in (
                "enable_postprocess_refine",
                "enable_postprocess_glossary_check",
                "enable_cover_generation",
                "enable_broadcast_date_agent_fallback",
            ):
                stack.enter_context(
                    patch.object(workflow_api.settings, setting, False)
                )
            stack.enter_context(
                patch.object(workflow_api.settings, "archived_path", None)
            )
            stack.enter_context(
                patch.object(workflow_api.settings, "package_path", None)
            )
            workflow_api.process_project(
                "demo", enable_live_chat=True, progress=NoopProgressReporter()
            )

        self.assertEqual(
            calls,
            [
                "metadata",
                "download",
                "combine",
                "chat_fetch",
                "audio",
                "asr",
                "srt",
                "prepass",
                "chunks",
                "finalize",
                "chat",
            ],
        )

    def test_fetch_rebases_on_recorded_section_and_rejects_unknown_cut(self):
        from workflow.stages import live_chat as chat_stage

        root = _temp_dir(self)
        project = MagicMock()
        project.full_video_path = root / "video.full.mp4"
        project.section_start = None
        project.section_end = None
        project.full_video_path.write_text("cut", encoding="utf-8")

        with (
            patch.object(chat_stage, "download_live_chat") as download,
            self.assertRaisesRegex(ValueError, "bounds were not recorded"),
        ):
            chat_stage.fetch_project_live_chat(project)
        download.assert_not_called()

        project.section_start = 1800.0
        project.section_end = 2100.0
        with (
            patch.object(chat_stage, "download_live_chat"),
            patch.object(chat_stage, "parse_live_chat") as parse,
        ):
            chat_stage.fetch_project_live_chat(project)
        self.assertEqual(
            parse.call_args.kwargs,
            {"section_start": 1800.0, "section_end": 2100.0},
        )

    def test_process_video_records_the_section(self):
        from workflow.stages import media as media_stage

        project = MagicMock()
        project.full_video_path.exists.return_value = True
        with (
            patch.object(media_stage, "MediaProcessor"),
            patch.object(media_stage.settings, "enable_official_subtitles", False),
        ):
            media_stage.process_video(
                project, section_start=1800.0, section_end=2100.0
            )
        project.update_section.assert_called_once_with(1800.0, 2100.0)


if __name__ == "__main__":
    unittest.main()
