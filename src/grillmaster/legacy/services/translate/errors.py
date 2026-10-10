"""Error types raised by the translation stages."""


class ChunkTranslationError(RuntimeError):
    """Raised when a chunk fails after exhausting its attempts or fix layer."""

    def __init__(
        self,
        message: str,
        *,
        retries: int = 0,
        chunk_index: int,
        total_chunks: int,
        from_index: int,
        to_index: int,
    ):
        super().__init__(message)
        self.retries = retries
        self.chunk_index = chunk_index
        self.total_chunks = total_chunks
        self.from_index = from_index
        self.to_index = to_index

    @property
    def chunk_label(self) -> str:
        return (
            f"[chunk {self.chunk_index + 1}/{self.total_chunks}] "
            f"index {self.from_index}–{self.to_index}"
        )


class TranslationError(RuntimeError):
    """Raised when chunk translation cannot produce the full translated SRT."""
