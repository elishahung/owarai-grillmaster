"""Speech recognition and the Japanese source SRT built from it.

Two providers (`core.asr_provider.AsrProvider`): `mai` (Azure's
MAI-Transcribe-2, the default) and `scribe` (ElevenLabs Scribe v2, the
backup). Each module sends audio, keeps the raw response as `asr.json`,
turns its own response shape into provider-neutral `model.WordToken`s and
names its SRT knobs; each also hears `watch_clip` spans (`model.Hearing`).
`providers` is the registry (and reads a kept `asr.json` by its shape);
`srt_builder` turns tokens into subtitle blocks without knowing the
provider; `timing` checks a response for collapsed timings. Nothing here reads settings: callers pass
credentials and paths.
"""
