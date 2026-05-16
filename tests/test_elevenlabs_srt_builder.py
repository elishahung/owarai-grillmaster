import json
import shutil
import unittest
from pathlib import Path

from services.elevenlabs.srt_builder import (
    SrtFormatOptions,
    _convert_payload_with_options,
    convert_file,
    convert_payload_to_srt,
)


def word(text, start, end, speaker="speaker_0"):
    return {
        "text": text,
        "start": start,
        "end": end,
        "type": "word",
        "speaker_id": speaker,
        "logprob": 0.0,
    }


class ElevenLabsSrtTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_elevenlabs_srt"
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_splits_japanese_hard_punctuation(self):
        payload = {
            "words": [
                word("これは", 0.0, 0.4),
                word("テスト", 0.4, 0.8),
                word("です。", 0.8, 1.0),
                word("次", 1.1, 1.3),
                word("です？", 1.3, 1.6),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("これはテストです。", srt)
        self.assertIn("次です？", srt)
        self.assertIn("00:00:00,000 --> 00:00:01,020", srt)
        self.assertIn("00:00:01,100 --> 00:00:02,100", srt)

    def test_merges_close_speaker_turns_as_dialogue_without_speaker_ids(self):
        payload = {
            "words": [
                word("馬の", 0.0, 0.3, "speaker_0"),
                word("頭企画", 0.3, 0.8, "speaker_0"),
                word("とかより", 0.8, 1.2, "speaker_0"),
                word("いいでしょ。", 1.2, 1.7, "speaker_0"),
                word("あれは", 1.95, 2.2, "speaker_1"),
                word("嫌だ", 2.2, 2.45, "speaker_1"),
                word("もう。", 2.45, 2.7, "speaker_1"),
            ]
        }

        # 0.25s gap between speakers — exceeds the production default
        # of 0.1s. Pass an explicit threshold to focus this test on the
        # merge-into-dialogue behavior rather than the threshold value.
        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(merge_speaker_turns_gap_s=0.5)
        )

        self.assertIn("-馬の頭企画とかよりいいでしょ。", srt)
        self.assertIn("-あれは嫌だもう。", srt)
        self.assertNotIn("speaker_0", srt)
        self.assertNotIn("speaker_1", srt)

    def test_does_not_start_segment_with_japanese_particle(self):
        payload = {
            "words": [
                word("酒", 79.06, 79.26),
                word("に", 79.26, 79.42),
                word("花", 79.42, 79.6),
                word("び", 79.6, 79.68),
                word("ら", 79.74, 79.84),
                word("を", 79.84, 79.96),
                word("落", 79.96, 80.1),
                word("と", 80.1, 80.22),
                word("し", 80.22, 80.76),
                word("、", 80.76, 80.76),
                word("風", 80.9, 81.12),
                word("流", 81.12, 81.52),
                word("な", 81.52, 81.86),
                word("花", 81.86, 82.24),
                word("見", 82.24, 82.36),
                word("を", 82.36, 84.14),
                word("。", 84.14, 84.14),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(max_segment_chars=20, max_segment_duration_s=4.0),
        )

        self.assertIn("花見を。", srt)
        self.assertNotIn("\nを。\n", srt)

    def test_merges_overlapping_blocks_from_zero_length_speaker_turns(self):
        # The original block layout this test asserts depends on the
        # 2-line cap. Pass it explicitly so the test exercises the
        # overlap-merge behavior under that cap, regardless of default.
        payload = {
            "words": [
                word("あ", 44.14, 44.2, "speaker_0"),
                word("り", 44.2, 44.28, "speaker_0"),
                word("が", 44.28, 44.42, "speaker_0"),
                word("と", 44.42, 44.52, "speaker_0"),
                word("う", 44.52, 44.53, "speaker_0"),
                word("。", 44.53, 44.53, "speaker_0"),
                word("来", 44.53, 44.53, "speaker_1"),
                word("た", 44.53, 44.53, "speaker_1"),
                word("よ", 44.53, 44.53, "speaker_1"),
                word("。", 44.53, 44.53, "speaker_1"),
                word("あ", 44.53, 44.53, "speaker_1"),
                word("り", 44.53, 44.53, "speaker_1"),
                word("が", 44.53, 44.53, "speaker_1"),
                word("と", 44.53, 44.53, "speaker_1"),
                word("う", 44.53, 44.53, "speaker_1"),
                word("。", 44.53, 44.53, "speaker_1"),
                word("援", 44.58, 44.7, "speaker_1"),
                word("軍", 44.7, 44.98, "speaker_1"),
                word("だ", 44.98, 45.3, "speaker_1"),
                word("。", 45.3, 45.3, "speaker_1"),
                word("い", 45.36, 45.46, "speaker_0"),
                word("い", 45.46, 45.56, "speaker_0"),
                word("で", 45.56, 45.62, "speaker_0"),
                word("し", 45.62, 45.68, "speaker_0"),
                word("ょ", 45.68, 45.96, "speaker_0"),
                word("、", 45.96, 45.96, "speaker_0"),
                word("今", 46.0, 46.04, "speaker_0"),
                word("日", 46.04, 46.18, "speaker_0"),
                word("は", 46.18, 46.34, "speaker_0"),
                word("。", 46.34, 46.34, "speaker_0"),
            ]
        }

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_lines_per_block=2)
        )

        self.assertIn("1\n00:00:44,140 --> 00:00:44,530", srt)
        self.assertIn("-ありがとう。", srt)
        self.assertIn("-来たよ。", srt)
        self.assertNotIn("00:00:44,530 --> 00:00:44,530", srt)

    def test_limits_dialogue_block_height_after_overlap_merge(self):
        payload = {
            "words": [
                word("一。", 0.0, 0.1, "speaker_0"),
                word("二。", 0.1, 0.1, "speaker_1"),
                word("三。", 0.1, 0.1, "speaker_1"),
                word("四。", 0.2, 0.2, "speaker_1"),
                word("五。", 0.3, 0.3, "speaker_0"),
                word("六。", 0.5, 0.6, "speaker_1"),
            ]
        }

        # The split between blocks is driven by the 2-line cap. Pass it
        # explicitly to keep this test pinned to that cap. With short
        # same-speaker hard-punct merging enabled, 三+四 land in one
        # block (inlined), and 五+六 form a final dialogue block.
        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_lines_per_block=2)
        )

        self.assertIn("1\n00:00:00,000 --> 00:00:00,450\n-一。\n-二。", srt)
        self.assertIn("2\n00:00:00,450 --> 00:00:00,550\n三。 四。", srt)
        self.assertIn("3\n00:00:00,550 --> 00:00:01,350\n-五。\n-六。", srt)
        # No 4th block — 五+六 together fit the dialogue cap.
        self.assertNotIn("\n4\n", srt.rstrip("\n") + "\n")

    def test_merges_short_same_speaker_utterances_across_hard_punct(self):
        # Two short same-speaker utterances back-to-back: previous ends
        # in 「？」, next ends in 「。」, both within the inline limits.
        # Should land in one block joined by a single space, not split
        # into two adjacent blocks with their own timestamps.
        payload = {
            "words": [
                word("い", 289.14, 289.38, "speaker_1"),
                word("い", 289.38, 289.48, "speaker_1"),
                word("ん", 289.48, 289.6, "speaker_1"),
                word("す", 289.6, 289.7, "speaker_1"),
                word("か", 289.7, 289.78, "speaker_1"),
                word("？", 289.78, 289.82, "speaker_1"),
                word("そ", 289.82, 289.92, "speaker_1"),
                word("れ", 289.92, 290.36, "speaker_1"),
                word("。", 290.36, 290.36, "speaker_1"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("いいんすか？ それ。", srt)
        self.assertNotIn("いいんすか？\nそれ。", srt)
        self.assertNotIn("\n2\n", srt.rstrip("\n") + "\n")

    def test_merges_short_same_speaker_utterances_with_small_gap(self):
        # Same-speaker pair separated by a 0.10s pause (within
        # merge_same_speaker_gap_s) and both ending in 「。」. Should
        # still merge and inline despite the small gap.
        payload = {
            "words": [
                word("そ", 343.18, 343.28, "speaker_1"),
                word("う", 343.28, 343.44, "speaker_1"),
                word("か", 343.44, 343.56, "speaker_1"),
                word("そ", 343.56, 343.62, "speaker_1"),
                word("う", 343.62, 343.74, "speaker_1"),
                word("か", 343.74, 343.82, "speaker_1"),
                word("。", 343.82, 343.82, "speaker_1"),
                word("ご", 343.92, 344.08, "speaker_1"),
                word("め", 344.08, 344.22, "speaker_1"),
                word("ん", 344.22, 344.28, "speaker_1"),
                word("ご", 344.28, 344.4, "speaker_1"),
                word("め", 344.4, 344.5, "speaker_1"),
                word("ん", 344.5, 344.58, "speaker_1"),
                word("。", 344.58, 344.58, "speaker_1"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("そうかそうか。 ごめんごめん。", srt)
        self.assertNotIn("そうかそうか。\nごめんごめん。", srt)
        self.assertNotIn("\n2\n", srt.rstrip("\n") + "\n")

    def test_inlines_short_repeated_same_speaker_utterances(self):
        payload = {
            "words": [
                word("何", 1320.72, 1320.88, "speaker_8"),
                word("？", 1320.88, 1320.9, "speaker_8"),
                word("何", 1320.9, 1320.91, "speaker_8"),
                word("？", 1320.91, 1321.02, "speaker_8"),
                word("何", 1321.02, 1321.52, "speaker_8"),
                word("？", 1321.52, 1323.22, "speaker_8"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("1\n00:22:00,720 --> 00:22:03,720\n何？ 何？ 何？", srt)
        self.assertNotIn("何？\n何？\n何？", srt)

    def test_keeps_long_same_speaker_utterances_stacked_after_overlap_merge(self):
        payload = {
            "words": [
                word("これは長めの質問です？", 0.0, 0.2, "speaker_0"),
                word("これも長めの返答です。", 0.2, 0.4, "speaker_0"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("これは長めの質問です？\nこれも長めの返答です。", srt)

    def test_does_not_merge_reply_when_dialogue_would_exceed_two_lines(self):
        payload = {
            "words": [
                word("ということで、", 22.72, 23.5, "speaker_0"),
                word("今回やるんやけど、", 23.6, 24.6, "speaker_0"),
                word("前は", 24.72, 25.32, "speaker_0"),
                word("1時間", 25.56, 26.02, "speaker_0"),
                word("2時間ぐらいかかって。", 26.14, 26.94, "speaker_0"),
                word("かかりました。", 27.02, 27.5, "speaker_1"),
            ]
        }

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_lines_per_block=2)
        )

        self.assertIn(
            "1\n00:00:22,720 --> 00:00:26,940\n"
            "ということで、今回やるんやけど、\n"
            "前は1時間2時間ぐらいかかって。",
            srt,
        )
        self.assertIn("2\n00:00:27,020 --> 00:00:28,000\nかかりました。", srt)
        self.assertNotIn("-かかりました。", srt)

    def test_keeps_short_sentence_tail_with_previous_long_utterance(self):
        payload = {
            "words": [
                word("続", 409.55, 409.65, "speaker_1"),
                word("い", 409.65, 409.75, "speaker_1"),
                word("て", 409.75, 409.83, "speaker_1"),
                word("決", 409.83, 410.07, "speaker_1"),
                word("勝", 410.17, 410.25, "speaker_1"),
                word("２", 410.29, 410.59, "speaker_1"),
                word("組", 410.59, 410.93, "speaker_1"),
                word("目", 410.93, 411.27, "speaker_1"),
                word("、", 411.27, 411.27, "speaker_1"),
                word("B", 411.31, 411.33, "speaker_1"),
                word("ブ", 411.59, 411.67, "speaker_1"),
                word("ロ", 411.67, 411.73, "speaker_1"),
                word("ッ", 411.73, 411.89, "speaker_1"),
                word("ク", 411.89, 412.03, "speaker_1"),
                word("を", 412.03, 412.15, "speaker_1"),
                word("勝", 412.15, 412.31, "speaker_1"),
                word("ち", 412.31, 412.45, "speaker_1"),
                word("上", 412.45, 412.49, "speaker_1"),
                word("が", 412.49, 412.63, "speaker_1"),
                word("っ", 412.63, 412.71, "speaker_1"),
                word("た", 412.71, 412.77, "speaker_1"),
                word("の", 412.77, 412.85, "speaker_1"),
                word("は", 412.85, 413.03, "speaker_1"),
                word("こ", 413.03, 413.25, "speaker_1"),
                word("の", 413.25, 413.39, "speaker_1"),
                word("コ", 413.39, 413.45, "speaker_1"),
                word("ン", 413.45, 413.55, "speaker_1"),
                word("ビ", 413.55, 413.69, "speaker_1"),
                word("で", 413.69, 413.81, "speaker_1"),
                word("す", 413.81, 413.83, "speaker_1"),
                word("。", 413.83, 413.83, "speaker_1"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("00:06:49,550 --> 00:06:54,330", srt)
        self.assertNotIn("\n2\n", srt)

    def test_keeps_question_tail_before_close_speaker_reply(self):
        payload = {
            "words": [
                word("さ", 1056.0, 1056.12, "speaker_9"),
                word("あ", 1056.12, 1056.34, "speaker_9"),
                word("皆", 1056.34, 1056.48, "speaker_9"),
                word("さ", 1056.48, 1056.56, "speaker_9"),
                word("ん", 1056.56, 1056.66, "speaker_9"),
                word("、", 1056.66, 1056.66, "speaker_9"),
                word("最", 1056.78, 1056.98, "speaker_9"),
                word("後", 1056.98, 1057.08, "speaker_9"),
                word("の", 1057.08, 1057.22, "speaker_9"),
                word("投", 1057.22, 1057.46, "speaker_9"),
                word("票", 1057.52, 1057.7, "speaker_9"),
                word("前", 1057.7, 1057.78, "speaker_9"),
                word("に", 1057.78, 1058.04, "speaker_9"),
                word("一", 1058.04, 1058.24, "speaker_9"),
                word("言", 1058.24, 1058.62, "speaker_9"),
                word("ず", 1058.62, 1058.68, "speaker_9"),
                word("つ", 1058.68, 1059.08, "speaker_9"),
                word("羊", 1059.08, 1059.32, "speaker_9"),
                word("寝", 1059.32, 1059.4, "speaker_9"),
                word("入", 1059.4, 1059.58, "speaker_9"),
                word("り", 1059.58, 1059.76, "speaker_9"),
                word("い", 1059.76, 1059.84, "speaker_9"),
                word("か", 1059.84, 1059.98, "speaker_9"),
                word("が", 1059.98, 1060.02, "speaker_9"),
                word("で", 1060.02, 1060.2, "speaker_9"),
                word("す", 1060.2, 1060.3, "speaker_9"),
                word("か", 1060.3, 1060.46, "speaker_9"),
                word("？", 1060.46, 1060.54, "speaker_9"),
                word("は", 1060.6, 1060.66, "speaker_10"),
                word("い", 1060.66, 1060.84, "speaker_10"),
                word("、", 1060.84, 1060.84, "speaker_10"),
                word("ちょっと", 1060.86, 1061.12, "speaker_10"),
                word("うちだけ", 1061.12, 1061.86, "speaker_10"),
                word("推薦人が", 1061.86, 1062.36, "speaker_10"),
                word("帰ってしまったんですけど。", 1062.36, 1063.5, "speaker_10"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("ですか？", srt)
        self.assertIn("2\n00:17:40,600", srt)
        self.assertNotIn("-すか？", srt)

    def test_prefers_soft_punctuation_when_duration_limit_is_hit(self):
        payload = {
            "words": [
                word("ク", 53.58, 53.62, "speaker_2"),
                word("イ", 53.62, 53.78, "speaker_2"),
                word("ズ", 53.78, 53.96, "speaker_2"),
                word("王", 53.96, 54.12, "speaker_2"),
                word("の", 54.12, 54.42, "speaker_2"),
                word("リ", 54.42, 54.66, "speaker_2"),
                word("ベ", 54.66, 54.78, "speaker_2"),
                word("ン", 54.78, 54.94, "speaker_2"),
                word("ジ", 54.94, 55.339, "speaker_2"),
                word("か", 55.34, 55.38, "speaker_2"),
                word("、", 55.38, 55.38, "speaker_2"),
                word("は", 56.4, 56.48, "speaker_2"),
                word("た", 56.48, 56.62, "speaker_2"),
                word("ま", 56.62, 56.72, "speaker_2"),
                word("た", 56.72, 57.3, "speaker_2"),
                word("芸", 57.3, 57.42, "speaker_2"),
                word("人", 57.42, 57.62, "speaker_2"),
                word("た", 57.62, 57.7, "speaker_2"),
                word("ち", 57.7, 57.82, "speaker_2"),
                word("が", 57.82, 58.18, "speaker_2"),
                word("跳", 58.18, 58.38, "speaker_2"),
                word("ね", 58.38, 58.46, "speaker_2"),
                word("返", 58.46, 58.86, "speaker_2"),
                word("す", 58.86, 58.96, "speaker_2"),
                word("か", 58.96, 59.14, "speaker_2"),
                word("。", 59.14, 59.14, "speaker_2"),
            ]
        }

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_segment_duration_s=4.0)
        )

        self.assertIn("クイズ王のリベンジか、", srt)
        self.assertIn("はたまた芸人たちが跳ね返すか。", srt)
        self.assertNotIn("はたまた芸\n\n", srt)

    def test_splits_leading_hard_punctuation_without_orphaning_year(self):
        payload = {
            "words": [
                word("リ", 17.96, 18.2, "speaker_2"),
                word("ベ", 18.2, 18.24, "speaker_2"),
                word("ン", 18.24, 18.36, "speaker_2"),
                word("ジ", 18.36, 18.5, "speaker_2"),
                word("に", 18.5, 18.62, "speaker_2"),
                word("燃", 18.62, 18.72, "speaker_2"),
                word("え", 18.72, 18.84, "speaker_2"),
                word("る", 18.84, 19.32, "speaker_2"),
                word("本", 19.32, 19.52, "speaker_2"),
                word("気", 19.52, 19.68, "speaker_2"),
                word("の", 19.68, 19.82, "speaker_2"),
                word("ク", 19.82, 19.9, "speaker_2"),
                word("イ", 19.9, 20.1, "speaker_2"),
                word("ズ", 20.1, 20.26, "speaker_2"),
                word("王", 20.26, 20.64, "speaker_2"),
                word("。1934", 20.64, 22.5, "speaker_2"),
                word("年", 22.5, 22.72, "speaker_2"),
                word("、", 22.72, 22.72, "speaker_2"),
                word("吉", 23.26, 23.44, "speaker_2"),
                word("本", 23.44, 23.72, "speaker_2"),
                word("が", 23.72, 24.279, "speaker_2"),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(
                max_segment_duration_s=4.0,
                segment_on_silence_longer_than_s=0.5,
            ),
        )

        self.assertIn("リベンジに燃える本気のクイズ王。", srt)
        self.assertIn("1934年、吉本が", srt)
        self.assertNotIn("\n1934年、\n", srt)

    def test_duration_limit_does_not_split_japanese_compounds(self):
        payload = {
            "words": [
                word("さ", 76.66, 76.72),
                word("あ", 76.72, 76.73),
                word("、", 76.73, 76.73),
                word("今", 76.82, 76.94),
                word("か", 76.94, 77.0),
                word("ら", 77.0, 77.08),
                word("皆", 77.08, 77.38),
                word("さ", 77.38, 77.44),
                word("ん", 77.44, 77.52),
                word("に", 77.52, 77.58),
                word("で", 77.58, 77.66),
                word("す", 77.66, 77.72),
                word("ね", 77.72, 77.86),
                word("、", 77.86, 77.86),
                word("様", 78.08, 78.2),
                word("々", 78.2, 78.56),
                word("な", 78.56, 78.7),
                word("パ", 78.7, 78.86),
                word("タ", 78.86, 78.92),
                word("ー", 78.92, 79.1),
                word("ン", 79.1, 79.3),
                word("で", 79.3, 79.759),
                word("吉", 79.76, 80.2),
                word("本", 80.2, 80.24),
                word("芸", 80.24, 80.58),
                word("人", 80.58, 80.82),
                word("を", 80.82, 80.94),
                word("お", 80.94, 81.039),
                word("見", 81.04, 81.24),
                word("せ", 81.3, 81.34),
                word("し", 81.34, 81.46),
                word("ま", 81.46, 81.6),
                word("す", 81.6, 81.72),
                word("の", 81.72, 81.86),
                word("で", 81.86, 82.14),
                word("、", 82.14, 82.14),
                word("そ", 82.2, 82.32),
                word("れ", 82.32, 82.54),
                word("が", 82.54, 82.9),
                word("誰", 82.9, 83.16),
                word("な", 83.16, 83.3),
                word("の", 83.3, 83.48),
                word("か", 83.48, 83.62),
                word("わ", 83.62, 83.74),
                word("か", 83.74, 83.9),
                word("っ", 83.9, 84.02),
                word("た", 84.02, 84.18),
                word("ら", 84.18, 84.36),
                word("早", 84.36, 84.5),
                word("押", 84.5, 84.74),
                word("し", 84.74, 84.92),
                word("で", 84.92, 85.6),
                word("お", 85.6, 85.74),
                word("答", 85.74, 86.02),
                word("え", 86.02, 86.08),
                word("く", 86.08, 86.24),
                word("だ", 86.24, 86.46),
                word("さ", 86.46, 86.64),
                word("い", 86.64, 87.1),
                word("。", 87.1, 87.1),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(max_segment_duration_s=4.0, max_lines_per_block=3),
        )

        self.assertIn("吉本芸人", srt)
        self.assertIn("早押し", srt)
        self.assertNotIn("吉本芸\n\n", srt)
        self.assertNotIn("早\n\n", srt)

    def test_zero_duration_limit_disables_duration_splitting(self):
        payload = {
            "words": [
                word("一", 0.0, 1.0),
                word("二", 1.0, 2.0),
                word("三", 2.0, 3.0),
                word("四", 3.0, 4.0),
                word("五", 4.0, 5.0),
                word("。", 5.0, 5.0),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(
                max_segment_duration_s=0.0,
                max_segment_chars=20,
                max_characters_per_line=20,
            ),
        )

        self.assertIn("一二三四五。", srt)
        self.assertNotIn("\n2\n", srt)

    def test_limits_close_three_speaker_turns_to_two_lines(self):
        payload = {
            "words": [
                word("一番流行ってるってスパイス。", 247.2, 248.48, "speaker_3"),
                word("ニューヨークで流行ってるスパイス？", 248.62, 250.46, "speaker_0"),
                word("そう。", 250.47, 250.8, "speaker_3"),
            ]
        }

        # Speaker gaps here are 0.14s and 0.01s — wider than the
        # production 0.1s threshold but typical for fast TV dialogue.
        # Pin both the gap and the 2-line cap so the test exercises
        # the line-cap logic on three close speaker turns.
        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(
                merge_speaker_turns_gap_s=0.5, max_lines_per_block=2
            ),
        )

        self.assertIn("-一番流行ってるってスパイス。", srt)
        self.assertIn("-ニューヨークで流行ってるスパイス？", srt)
        self.assertIn("2\n00:04:10,470 --> 00:04:11,320\nそう。", srt)
        self.assertNotIn("-そう。", srt)

    def test_does_not_merge_speaker_turns_when_gap_is_too_large(self):
        payload = {
            "words": [
                word("先です。", 0.0, 0.5, "speaker_0"),
                word("後です。", 2.0, 2.5, "speaker_1"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("1\n00:00:00,000 --> 00:00:01,000\n先です。", srt)
        self.assertIn("2\n00:00:02,000 --> 00:00:03,000\n後です。", srt)
        self.assertNotIn("-先です。", srt)

    def test_respects_common_segment_options(self):
        payload = {
            "words": [
                word("一", 0.0, 0.3),
                word("二", 0.3, 0.6),
                word("三", 0.6, 0.9),
                word("四", 0.9, 1.2),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(max_segment_chars=2, max_characters_per_line=2),
        )

        self.assertIn("1\n00:00:00,000 --> 00:00:00,600\n一二", srt)
        self.assertIn("2\n00:00:00,600 --> 00:00:01,700\n三四", srt)

    def test_convert_file_writes_srt(self):
        root = self._make_temp_dir()
        input_path = root / "asr.json"
        output_path = root / "video.ja.srt"
        input_path.write_text(
            json.dumps({"words": [word("はい。", 0.0, 0.4)]}, ensure_ascii=False),
            encoding="utf-8",
        )

        convert_file(input_path, output_path)

        self.assertEqual(
            output_path.read_text(encoding="utf-8"),
            "1\n00:00:00,000 --> 00:00:00,900\nはい。\n",
        )

    def test_raises_when_json_has_no_timed_words(self):
        with self.assertRaisesRegex(ValueError, "timed words"):
            convert_payload_to_srt({"words": [{"text": "はい", "type": "word"}]})

    def test_wrap_avoids_te_connector_split(self):
        # Block 5 from real ASR: 〜出資して〜 should not split between
        # the 連用形 stem and the connector 「て」.
        text = (
            "1934年、吉本が京成電鉄や東芝などと共同出資して"
            "設立した現在も残るプロスポーツチームは？"
        )
        payload = {"words": [word(text, 0.0, 11.5)]}

        srt = convert_payload_to_srt(payload)

        self.assertIn("1934年、吉本", srt)
        # Original buggy break: "出資し|て..." — line 2 starting with て.
        self.assertNotIn("出資し\nて", srt)
        # Broader: no line should start with the unsafe connector て.
        self.assertNotIn("\nて", srt)

    def test_wrap_avoids_orphan_ka_tail(self):
        # Block 15 from real ASR: 〜跳ね返すか。 — the original heuristic
        # wrapped at max_chars and orphaned 「か。」 onto its own line.
        text = "クイズ王のリベンジか、はたまた芸人たちが跳ね返すか。"
        payload = {"words": [word(text, 0.0, 5.5)]}

        srt = convert_payload_to_srt(payload)

        self.assertIn(
            "クイズ王のリベンジか、\nはたまた芸人たちが跳ね返すか。", srt
        )
        # Original buggy break.
        self.assertNotIn("跳ね返す\nか。", srt)

    def test_wrap_breaks_after_particle_and_avoids_orphan_de_tail(self):
        # Block 23 from real ASR: 〜見せますので、 — the original heuristic
        # orphaned 「で、」 onto its own line. The new heuristic should
        # break right after the を particle instead.
        text = "そこからゆっくりズームアウトする映像を見せますので、"
        payload = {"words": [word(text, 0.0, 4.5)]}

        srt = convert_payload_to_srt(payload)

        self.assertIn(
            "そこからゆっくりズームアウトする映像を\n見せますので、", srt
        )
        self.assertNotIn("見せますの\nで、", srt)
        self.assertNotIn("\nで、", srt)

    def test_silence_does_not_orphan_short_hard_punct_tail(self):
        # A number followed by silence and a counter+。 — without the
        # orphan-tail check the silence would split off a standalone
        # 「歳。」 block. With it, the whole "30歳。" stays as one
        # utterance because the upcoming tail is short and ends with
        # hard punctuation.
        payload = {
            "words": [
                word("30", 0.0, 0.5, "speaker_0"),
                word("歳。", 2.0, 2.5, "speaker_0"),  # 1.5s silence
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("30歳。", srt)
        self.assertNotIn("\n歳。\n", srt.rstrip("\n") + "\n")

    def test_silence_does_not_orphan_drawn_out_filler_before_short_tail(self):
        # Speaker holds 'さ' for 1.4s, pauses 1s, then says 'あ赤ちゃん。'.
        # The upcoming "あ赤ちゃん。" is a short hard-punct tail, so the
        # silence-driven split is suppressed and the whole phrase becomes
        # one utterance "さあ赤ちゃん。".
        payload = {
            "words": [
                word("前", 0.0, 0.4, "speaker_0"),
                word("の。", 0.4, 0.8, "speaker_0"),
                word("さ", 4.0, 5.4, "speaker_0"),
                word("あ", 6.4, 6.46, "speaker_0"),
                word("赤ちゃん。", 6.46, 6.88, "speaker_0"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("さあ赤ちゃん。", srt)
        self.assertNotIn("\nさ\n", srt.rstrip("\n") + "\n")

    def test_silence_does_not_split_before_bound_small_kana(self):
        # Speaker said 'ちょっと待ってください' but paused after 'ち'.
        # The next ASR token starts with 'ょ' — a small kana that's
        # orthographically locked to the preceding char. It must never
        # trigger a silence-driven utterance split.
        payload = {
            "words": [
                word("と", 0.0, 0.1, "speaker_0"),
                word("り", 0.1, 0.2, "speaker_0"),
                word("あ", 0.2, 0.3, "speaker_0"),
                word("え", 0.3, 0.4, "speaker_0"),
                word("ず", 0.4, 0.5, "speaker_0"),
                word("ち", 0.5, 0.7, "speaker_0"),
                word("ょ", 1.7, 1.8, "speaker_0"),  # 1s silence — would split
                word("っ", 1.8, 1.9, "speaker_0"),
                word("と", 1.9, 2.0, "speaker_0"),
                word("待って", 2.0, 2.4, "speaker_0"),
                word("ください。", 2.4, 2.8, "speaker_0"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("とりあえずちょっと待ってください。", srt)
        # The ASR-induced split between 'ち' and 'ょ' must NOT survive.
        self.assertNotIn("\nち\n", srt.rstrip("\n") + "\n")
        self.assertNotIn("ち\nょ", srt)

    def test_silence_does_not_orphan_drag_before_long_phrase(self):
        # Speaker holds 'そ' for ~0.8s, pauses 1.5s, then says a long
        # phrase that ends with '。'. The orphan-tail check can't help
        # (the upcoming phrase is too long to be a tail), so the
        # drag-current check is what prevents 'そ' from becoming its
        # own block.
        payload = {
            "words": [
                word("そ", 0.0, 0.82, "speaker_0"),
                word("う", 2.32, 2.40, "speaker_0"),
                word("いう感じで", 2.40, 2.90, "speaker_0"),
                word("私が好きな回もありまして。", 2.90, 4.20, "speaker_0"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        self.assertIn("そういう感じで私が好きな回もありまして。", srt)
        self.assertNotIn("\nそ\n", srt.rstrip("\n") + "\n")

    def test_split_at_terminal_hard_punct_when_segment_overflows(self):
        # When current already ends with '。' and adding the next token
        # would exceed max_segment_chars, the natural split point is
        # right after the '。'. Earlier code skipped the last token in
        # _find_best_utterance_split_index so it fell back to an
        # earlier '、', merging two separate sentences.
        payload = {
            "words": [
                word("これは長めの文章です。", 0.0, 2.5, "speaker_0"),
                word("そして二つ目の長めの文章です。", 4.0, 6.5, "speaker_0"),
            ]
        }

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_segment_chars=20)
        )

        # Two separate utterances → not joined into one line.
        self.assertNotIn(
            "これは長めの文章です。そして二つ目の長めの文章です。", srt
        )
        self.assertIn("これは長めの文章です。", srt)
        self.assertIn("そして二つ目の長めの文章です。", srt)

    def test_long_utterance_wraps_at_clause_boundaries_not_mid_word(self):
        # A 50-char utterance must wrap to 3 lines. Earlier the first
        # line would greedy-cut at max_chars, splitting `言った` into
        # `言っ` / `た`. The relaxed `lo` lets the scorer find a clean
        # break at '、' instead.
        text = (
            "うん、それでいいって言って、"
            "この人がワーって言ったタイミングで"
            "今の高橋私はめっちゃ好きって言ってる。"
        )
        payload = {"words": [word(text, 0.0, 7.0, "speaker_0")]}

        srt = convert_payload_to_srt(payload)

        self.assertNotIn("言っ\nた", srt)
        # Verb stem 言っ followed by past-tense た must stay together.
        self.assertIn("言った", srt)

    def test_silence_split_still_fires_when_upcoming_tail_is_long(self):
        # A different-speaker boundary still splits, even if the next
        # speaker's first utterance is short.
        payload = {
            "words": [
                word("どうぞ。", 0.0, 0.5, "speaker_0"),
                word("さ", 1.0, 2.4, "speaker_1"),
                word("はい。", 4.0, 4.5, "speaker_2"),
            ]
        }

        srt = convert_payload_to_srt(payload)

        # speaker_1's standalone 'さ' stays in its own block.
        self.assertIn("\nさ\n", srt.rstrip("\n") + "\n")

    def test_wrap_does_not_split_alphanumeric_run(self):
        # A run of digits near the natural midpoint must stay intact.
        text = "短い前置きですが12345という数字を含む長い文章である"
        payload = {"words": [word(text, 0.0, 5.0)]}

        srt = convert_payload_to_srt(payload)

        # The contiguous digit run survives — `in` won't span newlines,
        # so this asserts the digits all live on a single line.
        self.assertIn("12345", srt)

    def test_hold_extends_last_block_by_full_amount(self):
        # No following block to constrain — the only block gets the
        # full hold extension applied to its end time.
        payload = {"words": [word("はい。", 0.0, 0.4)]}

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(
                subtitle_hold_after_end_s=0.6,
                min_inter_subtitle_gap_s=0.08,
            ),
        )

        self.assertIn("00:00:00,000 --> 00:00:01,000\nはい。", srt)

    def test_hold_caps_middle_block_at_next_block_minus_min_gap(self):
        # Block 1 (0.0→0.5) wants to extend by hold=0.5 to 1.0, but
        # block 2 starts at 0.8 — cap is 0.8 - min_gap(0.08) = 0.72.
        # Block 2 (last) gets the full hold to 1.5 + 0.5 = 2.0.
        payload = {
            "words": [
                word("先です。", 0.0, 0.5, "speaker_0"),
                word("後です。", 0.8, 1.5, "speaker_1"),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(
                subtitle_hold_after_end_s=0.5,
                min_inter_subtitle_gap_s=0.08,
            ),
        )

        self.assertIn("1\n00:00:00,000 --> 00:00:00,720\n先です。", srt)
        self.assertIn("2\n00:00:00,800 --> 00:00:02,000\n後です。", srt)

    def test_hold_does_not_shrink_back_to_back_blocks(self):
        # Blocks 1-2 sit at 0.0→0.45 and 0.45→0.55 (no gap between
        # them). cap = next.start - 0.08 < block.end, so the no-shrink
        # rule keeps each end exactly as the segmenter laid it out.
        # Only the last block (0.55→0.85) extends by hold to 1.35.
        payload = {
            "words": [
                word("一。", 0.0, 0.1, "speaker_0"),
                word("二。", 0.1, 0.1, "speaker_1"),
                word("三。", 0.1, 0.1, "speaker_1"),
                word("四。", 0.2, 0.2, "speaker_1"),
                word("五。", 0.3, 0.3, "speaker_0"),
                word("六。", 0.5, 0.6, "speaker_1"),
            ]
        }

        srt = _convert_payload_with_options(
            payload,
            SrtFormatOptions(
                max_lines_per_block=2,
                subtitle_hold_after_end_s=0.5,
                min_inter_subtitle_gap_s=0.08,
            ),
        )

        self.assertIn("1\n00:00:00,000 --> 00:00:00,450", srt)
        self.assertIn("2\n00:00:00,450 --> 00:00:00,550", srt)
        self.assertIn("3\n00:00:00,550 --> 00:00:01,350", srt)
        self.assertNotIn("\n4\n", srt.rstrip("\n") + "\n")

    def test_hold_disabled_when_zero(self):
        # subtitle_hold_after_end_s=0 disables the extension pass —
        # block ends remain at the exact ASR speech end times.
        payload = {
            "words": [
                word("先です。", 0.0, 0.5, "speaker_0"),
                word("後です。", 2.0, 2.5, "speaker_1"),
            ]
        }

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(subtitle_hold_after_end_s=0)
        )

        self.assertIn("1\n00:00:00,000 --> 00:00:00,500\n先です。", srt)
        self.assertIn("2\n00:00:02,000 --> 00:00:02,500\n後です。", srt)

    def test_caps_long_single_utterance_to_two_lines_at_render(self):
        # A single utterance with no internal punctuation between the
        # leading 、 and trailing 。 stays > max_segment_chars (the
        # segmenter intentionally leaves a few such cases). It must
        # still render at most max_lines_per_block (2) lines.
        text = (
            "さあ、 THE SECOND 漫才トーナメント "
            "2026 一回戦 第一試合の 対戦カードはこちらです。"
        )
        payload = {"words": [word(text, 2270.86, 2277.28, "speaker_13")]}

        srt = convert_payload_to_srt(payload)

        # Single block, exactly two text lines, no blank line.
        self.assertNotIn("\n\n", srt.strip())
        body_lines = srt.strip().split("\n")[2:]
        self.assertEqual(len(body_lines), 2)
        # Tail not orphaned; ASCII tokens not split mid-run.
        self.assertIn("対戦カードはこちらです。", srt)
        self.assertIn("THE SECOND", srt)
        self.assertIn("2026", srt)
        # The pre-fix 3-line layout broke after 漫才トーナメント.
        self.assertNotIn("漫才トーナメント\n2026 一回戦", srt)

    def test_render_line_cap_respects_max_lines_per_block_override(self):
        # The cap generalizes: with max_lines_per_block=3 an over-long
        # no-punctuation utterance reflows to exactly 3 balanced lines.
        text = "検証用文章" * 16  # 80 chars, no punctuation/particles
        payload = {"words": [word(text, 0.0, 8.0)]}

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_lines_per_block=3)
        )

        self.assertNotIn("\n\n", srt.strip())
        body_lines = srt.strip().split("\n")[2:]
        self.assertEqual(len(body_lines), 3)
        self.assertEqual("".join(body_lines), text)

    def test_line_cap_does_not_change_merge_decisions(self):
        # Regression sentinel: the render-time line cap must NOT leak
        # into _rendered_line_count (the merge predictor). If it did,
        # the reply would merge into the previous block and the
        # boundaries/timecodes would change.
        payload = {
            "words": [
                word("ということで、", 22.72, 23.5, "speaker_0"),
                word("今回やるんやけど、", 23.6, 24.6, "speaker_0"),
                word("前は", 24.72, 25.32, "speaker_0"),
                word("1時間", 25.56, 26.02, "speaker_0"),
                word("2時間ぐらいかかって。", 26.14, 26.94, "speaker_0"),
                word("かかりました。", 27.02, 27.5, "speaker_1"),
            ]
        }

        srt = _convert_payload_with_options(
            payload, SrtFormatOptions(max_lines_per_block=2)
        )

        self.assertIn(
            "1\n00:00:22,720 --> 00:00:26,940\n"
            "ということで、今回やるんやけど、\n"
            "前は1時間2時間ぐらいかかって。",
            srt,
        )
        self.assertIn("2\n00:00:27,020 --> 00:00:28,000\nかかりました。", srt)
        self.assertNotIn("-かかりました。", srt)


if __name__ == "__main__":
    unittest.main()
