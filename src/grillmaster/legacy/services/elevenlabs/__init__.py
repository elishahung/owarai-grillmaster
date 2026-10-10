__all__ = [
    "ElevenLabsASR",
    "ElevenLabsTranscriptionResult",
    "convert_file",
]


def __getattr__(name: str):
    if name == "ElevenLabsASR":
        from .asr import ElevenLabsASR

        return ElevenLabsASR
    if name == "ElevenLabsTranscriptionResult":
        from .asr import ElevenLabsTranscriptionResult

        return ElevenLabsTranscriptionResult
    if name == "convert_file":
        from .srt_builder import convert_file

        return convert_file
    raise AttributeError(name)
