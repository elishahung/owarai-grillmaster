"""ElevenLabs speech recognition and the Japanese source SRT built from it.

`client` sends the audio to ElevenLabs Scribe and keeps the raw response as
`asr.json`; `srt_builder` turns that response's word timings into subtitle
blocks. Both read the response through `payload`, the one place that knows
where the words live. Nothing here reads settings: callers pass `AsrOptions`
and the API key.
"""
