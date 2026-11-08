"""Speech recognition and the Japanese source SRT built from it.

`mai` sends the episode's audio to Azure's MAI-Transcribe-2, keeps the raw
response as `asr.json` and turns it into provider-neutral
`model.WordToken`s; `srt_builder` turns those into subtitle blocks;
`timing` checks a response for collapsed timings. `scribe` (ElevenLabs
Scribe v2) only hears `watch_clip` spans (`model.Hearing`), a second
recognizer beside the episode's. Nothing here reads settings: callers pass
credentials and paths.
"""
