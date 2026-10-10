"""Core SRT block data type."""

from pydantic import BaseModel


class SrtBlock(BaseModel):
    """A single SRT subtitle entry: index, timecode, and text body."""

    index: int
    timecode: str
    text: str

    @property
    def raw(self) -> str:
        """Serialize this block back to SRT format (without trailing blank line)."""
        return f"{self.index}\n{self.timecode}\n{self.text}"

    @property
    def char_count(self) -> int:
        """Character count of the raw representation; used for chunk sizing."""
        return len(self.raw)
