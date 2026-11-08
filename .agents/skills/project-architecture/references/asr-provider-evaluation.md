# ASR provider evaluation

MAI-Transcribe-2 is the default ASR provider and ElevenLabs Scribe v2 the
backup (`[asr] provider`). This file records
two rounds of evaluation, so a later candidate is checked against the same
failures first instead of re-running a whole comparison:

- **2026-10-10:** Grok Voice Transcribe 2.0 was rejected for dropping speech.
- **2026-11-09:** MAI-Transcribe-2 passed and became the default.

## Candidates and outcome

| Candidate | Price (batch) | Outcome |
|---|---|---|
| ElevenLabs Scribe v2 | $0.22/h, keyterms +$0.05/h | backup; `watch_clip`'s second hearing |
| xAI Grok Voice Transcribe 2.0 | $0.10/h, keyterms/diarization included | rejected: drops speech |
| Microsoft MAI-Transcribe-2 (Azure Speech) | $0.10/h promo until 2026-12-31 | default since 2026-11-09 |

An earlier round had already ranked Scribe v2 above Doubao, Fun-ASR,
Chirp 3, GPT-4o-transcribe and self-hosted Qwen3-ASR. 104 archived
`asr.json` files from that era are Fun-ASR responses (`file_url`,
`properties`, `transcripts`), not Scribe.

## Round 1 (2026-10-10): Grok

### Method

- Samples came from 2026-09/10 projects:
  - 10-minute windows of four episodes: heavy cross-talk, singing, a dense
    three-person talk, and a YouTube upload with no talent metadata.
  - Two full episodes, 46 and 110 minutes long.
- Scribe's existing `asr.json` was sliced to each window, so the baseline
  cost nothing.
- Grok ran with and without keyterms. The keyterms came from `source`
  metadata: talent names, series, and 「」/■ titles from the title and
  description, minus hiragana-heavy quoted lines.
- Total spend was about $0.43.
- Both outputs went through `build_srt_blocks`, then automatic metrics:
  - character and digit counts;
  - speech-seconds present in one output only;
  - speaker count;
  - mid-sentence gaps of 3 s or more;
  - slow blocks;
  - keyterm counts.
- Disputed regions were cut to short clips and transcribed blind by
  Gemini 3.1 Pro through `agy`, on the subscription with no API cost.

### Findings

- **Grok silently drops speech.**
  - In the 110-minute episode, 1,178 s of speech appeared only in Scribe's
    output.
  - In a 10-minute YouTube window, Grok produced half of Scribe's
    characters.
  - Gemini confirmed that the missing regions held real speech: narration
    over loud music, and also clean dialogue with no music at all.
  - Lowering `vad_threshold` to 0, or turning off `diarize` or `format`, did
    not help.
  - Keyterms made it drop more: 2,393 → 2,156 and 1,733 → 1,381 characters
    on two windows.
- **Grok handles digits better.** Scribe sometimes drops digits (第4弾 → 第弾,
  4年目 → 年目; 5 cases in 2 of 38 episodes). Grok kept them.
- **Scribe mis-times sentence tails.**
  - It sometimes places a sentence's last characters tens of seconds after
    the rest. This happened 137 times over 38 episodes for gaps of 3 s or
    more, and the worst case produced a 77 s block.
  - Blind listening by Gemini found the tail really spoken with its sentence
    in 9 of 10 gaps. In the exception, the sentence head was sung and the
    tail spoken later.
  - A scribe_v2-only SRT compensation that capped such inner silences at 1 s
    was added in commit d535f17, with its measurements. It was reverted on
    2026-10-19, and the SRT builder uses the ASR timings as they are.

A 2026-10-16 recheck used Grok as the proofread `transcribe` engine on 9
short ASR-suspect clips of 8–25 s, and it confirmed the result:

- Grok kept calm speech but dropped shouted and overlapping lines whole,
  with or without VAD and diarization.
- gemini-3.8-flash heard those lines (イライパ for らいぱち, 空の咳 for 空の席).

## Round 2 (2026-11-09): MAI-Transcribe-2

Round 2 asked two questions:

1. Does MAI avoid Scribe's two timing failures?
   - **Stretched words:** one character spans a silence, so a subtitle block
     is held for tens of seconds or minutes.
   - **Collapsed stacks:** 100+ words share one timestamp. Since 400665f the
     `asr` stage discards such a response.
2. Does diarization hold on 90 minutes of audio? That is the longest single
   ASR request. Longer videos go through `--parts`.

Public reports before the round said diarization failed (HTTP 408/500/503)
somewhere between 15 and 33 minutes, and Azure documents no limit.

### Method

**Failure inventory.** All 944 archived Scribe responses were rebuilt with
the current `build_srt_blocks`. That found:

- 45 blocks of 30 s or more;
- 1,571 blocks of 10 s or more, many of them songs or long monologues;
- 4 responses with collapsed stacks.

**Samples.** Eleven episodes, taken from the worst cases plus long
many-speaker episodes, each capped at 90 minutes:

- 240913 友近サスペンス
- 260426 p359
- 260504 あんスタ
- 260606 THE トークワン
- 260628 らいぱち
- 260926 格付け
- 260928 MAD5
- 260928 シュシュ生配信
- 261006 華大千鳥
- 261107 M-1 3回戦
- 261108 p385

**Request.**

- Fast transcription (`transcriptions:transcribe`, API 2025-10-15) in
  westus.
- `enhancedMode.model = "MAI-Transcribe-2"` with
  `modelOptions {timestamps: "word", transcribeStyle: "verbatim"}`.
- `diarization.enabled`, `locales ["ja"]`, and no phrase list unless stated.
- Audio was 16 kHz mono FLAC cut from the archived `audio.ogg`. MAI takes no
  Opus, and 90 min of FLAC is about 200 MB.
- Scribe's archived `asr.json` was the baseline.

**Metrics.**

- Character and digit counts.
- Speech-seconds in one output only. Each word's end was capped at
  0.3 s/char + 0.5 s, so a stretched word does not count as speech.
- Stretched words: an end 3 s or more past that cap.
- Text-aligned start offsets. Windowed `SequenceMatcher` blocks of 6+
  characters were used, skipping text repeated inside the window.
- Mid-sentence gaps and collapsed stacks.
- Speaker-turn agreement within 1 s.

**Listening.** 18 disputed clips were transcribed blind by
gemini-3.1-pro-preview through the `gemini` CLI. On silence or music the CLI
sometimes returns unrelated text (Chinese, Thai, patent prose), so only
consistent answers were used.

**Spend.** 21.8 audio hours, about $2.18. The listening ran on the
subscription.

### Results

**90-minute diarization passed.**

- 11 of 11 requests on 5 episodes of 86–90 minutes returned 200 in 91–117 s.
- Re-running an episode gave near-identical output.
- This is one night's sample (02:00–02:19 +08:00). A 2026-11-05 report
  described every diarized request failing in southeastasia, so the service
  can fail as a whole.
- Speed matches Scribe: about 1.0–1.3 s per audio minute, upload included.
  The archived Scribe `asr` stage took a median of 1.26 s per minute over
  181 projects.

**Timing.** At every Scribe failure point checked, MAI placed the words
where they are spoken:

| Episode / point | Scribe | MAI | Evidence |
|---|---|---|---|
| 240913 66:20 | 「方島グループです」 stretched 3980→4022 s | at 4019 s | 3972–3990 s silent; the line heard at 4015–4024 s |
| 261006 8:38 | 「何を言うてま｜すの」 split 518 s / 594 s → 77 s block | both the scene and its replay at 588 s, each in place | text and listening |
| 260926 4:30, 14:15 | 「演｜奏」, 「か｜ら」 split by ~77 s | in place | text |
| 260628 32:55 | 「前」 stretched 76 s; the zh line shown 87 s early | the line at 2056–2069 s | listening |
| 260504 6:20 | 「今」 stretched 207 s | nothing | three 30 s samples silent |
| 260928 シュシュ 70:42 | 「ちょっと一旦タバコ行っ」 stretched 166 s | nothing (see misses) | 4300/4380 s samples silent |
| 260606 13:25, 260928 MAD5 44:59, 261107 44:02 | collapsed stacks (330, 136, 124 words) | spread at the spoken times; MAD5 also separates lyrics from talk | listening (260606) and text |

Elsewhere the two agree:

- Median start offset (MAI − Scribe) is −0.04 to −0.07 s, with p95 between
  0.2 and 1.1 s.
- 72–94 % of Scribe's characters align.
- Scribe had 0–22 stretched words per episode, up to 241 s excess.
  MAI had 0–1.

**Speech coverage.**

- MAI writes 5–35 % more characters.
- Listening to MAD5's MAI-only regions found real background talk,
  back-channels and lyrics.
- The 17 s of repeated うわ in p359 (84:00) is a real crowd reaction,
  over-repeated.
- One MAI miss was found: the シュシュ 70:42 line, which Scribe has. The
  owner judged it background noise (see the owner review below).
- Scribe had no long MAI-only gaps beyond stretched words.

**Digits.** MAI keeps digits Scribe drops (4組 → 組, 58億円 → 億円). Scribe
writes small counts in kanji (十回, 一本) where MAI writes 10回 and 1本.

**Names and homophones.** Neither provider is consistently better:
伯方島/博多島, 美咲ミカ/三崎美香, 前田真央/前野で百恵. The briefing's cast as
`phraseList` changed almost nothing on 261006 (15,933 → 15,885 characters).

**Diarization.**

- MAI finds 7–11 speakers where Scribe finds 14–28, which over-splits.
- MAI has 2–5× Scribe's turns, and 93–98 % of Scribe's turns are matched by
  a MAI turn.
- The extra turns are mostly back-channels and quick exchanges that Scribe
  folds into one speaker. In p385 at 20:00, one person talks while another
  answers うん and はい.

**Owner review (2026-11-09).** The owner watched seven clips. Each had both
providers' Japanese and speakers and the archived zh subtitles burned in.
All six questions went MAI's way, or were acceptable for MAI:

- **p359 28:25:** two people trade 頼む and 行け, as MAI splits it. Scribe has
  one speaker.
- **p385 19:50:** someone back-channels while 大悟 talks, as MAI splits it.
- **MAD5 82:15:** the MAI-only talk (藤田さん…奈美子) is worth subtitling.
- **らいぱち 32:52 / 34:10:** MAI's time for the line is correct.
- **華大千鳥 8:32:** MAI is right.
- **シュシュ 70:42:** the line MAI missed begins where Scribe's does. By
  then it is a group walking off into background noise, so leaving it
  unsubtitled is acceptable.

**Response shape.** A future SRT builder depends on these points:

- `phrases[]` hold `speaker`, `offsetMilliseconds`, `durationMilliseconds`,
  `text` and `words[]`. A phrase is one speaker segment, about 1.7 s on
  average.
- Words are single characters, and punctuation counts as a word. Words have
  no speaker.
- The whole p385 response came back with every phrase offset and duration
  set to 0, while its word times were correct. Words occasionally fall
  outside their phrase's span. Build from word times.
- `combinedPhrases` holds the full text.

### Adoption (2026-11-09)

- MAI is the default `[asr] provider`, Scribe the backup, switched by
  config. The promo price ends 2026-12-31. Each project keeps the response
  it has, and `transcript` reads it by its shape.
- The SRT builder stayed one provider-neutral module. Each provider maps its
  response to the same per-character tokens; MAI word times are used, never
  phrase times. The split left Scribe's `ja.srt` and `ja.voiced.srt`
  byte-identical over 945 archived responses.
- MAI's SRT differences live in `asr/mai.py`:
  - `merge_speaker_turns_gap_s = 0.2` (Scribe 0.05). It was measured the way
    Scribe's was: the share of replies that start with a response cue stays
    at 31–35 % up to 0.2 s, then falls.
  - `place_backchannels`. MAI gives a listener's うん/はい its own speaker,
    which split the talker's sentence in two blocks: 526 cases in 11
    episodes. The rule acts only on an other-speaker run of closed-list
    back-channel words inside an unfinished sentence:
    - when the sentence fits one line and is cut once, the run moves behind
      the sentence and becomes the block's second `-` line (111);
    - otherwise it is dropped (415).
  - No token's time changes. The owner reviewed before/after clips of each
    case and preferred the result.
- The episode's phrase list is the source's credited talent names. The
  briefing does not exist yet at ASR time. On 261006 the phrase list moved
  ノブ from 9 to 24 hits and 大悟 from 0 to 7, with no extra charge.
- The FLAC upload is made from `audio.ogg` and deleted after the request.
  The agents' Opus `audio.ogg` stays.
- A 408/429/5xx response, or a connection failure, is tried once more. Then
  the stage fails.
- `watch_clip` hears with the other provider (`[watch_clip] speech`,
  `auto` by default, so switching `[asr]` alone swaps both). The config
  refuses a named speech provider equal to `[asr]`. The prompt calls 発話 "about as
  accurate as `ja`, possibly less" when Scribe hears MAI's `ja`, and
  "possibly more" the other way.

## Re-evaluating a new candidate

1. Run it on the long MX final and one YouTube window. Compare the
   speech-seconds it has against Scribe's before looking at accuracy, since
   dropped speech is the disqualifying failure.
2. Check digits (第N弾, N年目), stretched words and collapsed stacks against
   the Round 2 failure points above.
3. A new provider is a module beside `asr/mai.py` and `asr/scribe.py`:
   client, `is_response`, `timed_words`, `srt_tokens` and SRT options, plus
   an entry in `asr/providers.py`.

API notes, in case a candidate is revisited:

- **Grok:** `POST https://api.x.ai/v1/stt`, sent as multipart with the file
  field last. Words come one character at a time, and times are in seconds.
- **MAI:**
  - Endpoint: `{custom-domain}.cognitiveservices.azure.com/speechtotext/transcriptions:transcribe?api-version=2025-10-15`,
    multipart with `audio` and a JSON `definition`.
  - No deployment step is needed. Times are in milliseconds.
  - The REST reference caps input at 2 hours and 250 MB.
  - It uses the `AZURE_SPEECH_KEY` and `AZURE_SPEECH_ENDPOINT` that the
    proofread's `watch_clip` already reads (`agent_tools/mai.py`).
