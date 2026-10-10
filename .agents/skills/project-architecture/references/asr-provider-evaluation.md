# ASR provider evaluation (2026-10-10)

ElevenLabs Scribe v2 stays the only ASR provider. This records why, so a later
candidate is checked against the same failure first instead of re-running the
whole comparison.

## Candidates and outcome

| Candidate | Price (batch) | Outcome |
|---|---|---|
| ElevenLabs Scribe v2 (current) | $0.22/h, keyterms +$0.05/h | kept |
| xAI Grok Voice Transcribe 2.0 | $0.10/h, keyterms/diarization included | rejected: drops speech |
| Microsoft MAI-Transcribe-2 (Azure Speech) | $0.10/h promo until 2026-12-31 | not evaluated further: public preview, unknown price after the promo, diarization unreliable on long audio |

An earlier round (see `docs/article.md`) had already ranked Scribe v2 above
Doubao, Fun-ASR, Chirp 3, GPT-4o-transcribe and self-hosted Qwen3-ASR.

## Method

- Samples from 2026-09/10 projects: 10-minute windows of four episodes
  (heavy cross-talk, singing, a dense three-person talk, a YouTube upload with
  no talent metadata) and two full episodes (46 and 110 minutes).
- Scribe's existing `asr.json` was sliced to each window, so the baseline cost
  nothing. Grok ran with and without keyterms built from `source` metadata
  (talent names, series, 「」/■ titles from title and description, minus
  hiragana-heavy quoted lines). Total spend: about $0.43.
- Both outputs went through `build_srt_blocks`, then automatic metrics:
  character and digit counts, speech-seconds present in one output only,
  speaker count, mid-sentence gaps of 3 s or more, slow blocks, keyterm counts.
- Disputed regions were cut to short clips and transcribed blind by
  Gemini 3.1 Pro through `agy` (subscription, no API cost).

## Findings

- **Grok silently drops speech.** In the 110-minute episode, 1,178 s of speech
  appeared only in Scribe's output; in a 10-minute YouTube window, Grok
  produced half of Scribe's characters. Gemini confirmed that the missing
  regions held real speech: narration over loud music, and also clean
  dialogue with no music at all. Lowering `vad_threshold` to 0, or turning
  off `diarize` or `format`, did not help. Keyterms made it drop more
  (2,393 → 2,156 and 1,733 → 1,381 characters on two windows).
- **Grok handles digits better.** Scribe sometimes drops digits (第4弾 → 第弾,
  4年目 → 年目; 5 cases in 2 of 38 episodes); Grok kept them.
- **MAI looked accurate in a 40 s smoke test.** It kept digits and names that
  the other two got wrong. Community reports show diarization failing
  (HTTP 408/500/503) somewhere between 15 and 33 minutes, and the REST
  reference caps input at 2 hours.
- **Scribe mis-times sentence tails.** It sometimes places a sentence's last
  characters tens of seconds after the rest. This happened 137 times over
  38 episodes for gaps of 3 s or more, and the worst case produced a 77 s
  block. `asr/srt_compensation.py` compensates for it, for `scribe_v2` only; the rule
  and the rejected alternatives are in its docstring. Measured over the same
  38 episodes, block texts and start times stayed the same except for 45
  dialogue blocks that are now split:

  | Measure | Before | After |
  |---|---|---|
  | Blocks of 10 s or more | 27 | 6 |
  | Slow blocks (4 s or more, under 1.5 chars/s) | 79 | 12 |
  | Longest block | 76.9 s | 12.6 s |
  | Blocks under 0.6 s | 1171 | 1172 |

  The user also compared 23 clips side by side and judged the new timing
  better. Blind listening by Gemini found the tail really spoken with its
  sentence in 9 of 10 gaps; in the exception, the sentence head was sung
  and the tail spoken later.

## Re-evaluating a new candidate

1. Run it on the long MX final and one YouTube window. Compare the
   speech-seconds it has against Scribe's before looking at accuracy, since
   dropped speech is the disqualifying failure.
2. Check digits (第N弾, N年目) and the mid-sentence gap count. Build the
   candidate's SRTs with the plain `SrtFormatOptions()`: the
   `srt_options_for_model` compensation targets Scribe v2's fault only.
3. A provider abstraction is not built yet. `transcript` and everything after
   it need only `words[{text, start, end, type, speaker_id}]` and
   `audio_duration_secs`; ElevenLabs' raw response shape is the de facto
   schema of `asr.json`.

API notes, in case a candidate is revisited:

- **Grok:** `POST https://api.x.ai/v1/stt`, sent as multipart with the file
  field last. Words come one character at a time, and times are in seconds.
- **MAI:** `{custom-domain}.cognitiveservices.azure.com/speechtotext/transcriptions:transcribe?api-version=2025-10-15`,
  with `enhancedMode.model = "MAI-Transcribe-2"`. No deployment step is
  needed, and it is available in westus. Times are in milliseconds.

The `XAI_API_KEY`, `AZURE_API_KEY` and `AZURE_ENDPOINT` entries in `.env`
were for this evaluation only; no code reads them.
