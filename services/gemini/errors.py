"""Error and summary types for Gemini translation cost tracking."""

from pydantic import BaseModel, Field


class TranslationCostSummary(BaseModel):
    total_cost: float
    pre_pass_cost: float
    chunk_costs: list[float] = Field(default_factory=list)
    num_chunks: int
    retries: int
    elapsed_seconds: float
    completed_chunks: int
    failed_chunks: list[str] = Field(default_factory=list)


class CostTrackingError(RuntimeError):
    """Base error that preserves accumulated API cost."""

    def __init__(self, message: str, accumulated_cost: float = 0.0):
        super().__init__(message)
        self.accumulated_cost = accumulated_cost


class PrePassError(CostTrackingError):
    """Raised when Gemini pre-pass fails after accruing cost."""


class ChunkTranslationError(CostTrackingError):
    """Raised when a chunk fails after accruing cost."""

    def __init__(
        self,
        message: str,
        *,
        accumulated_cost: float = 0.0,
        retries: int = 0,
        chunk_index: int,
        total_chunks: int,
        from_index: int,
        to_index: int,
    ):
        super().__init__(message, accumulated_cost=accumulated_cost)
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


class GeminiTranslationError(RuntimeError):
    """Raised when translation fails with a partial cost summary."""

    def __init__(self, message: str, summary: TranslationCostSummary):
        super().__init__(message)
        self.summary = summary
