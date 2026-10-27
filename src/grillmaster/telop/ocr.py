"""GPU OCR of the sampled frames: PP-OCRv5 server detection with PP-OCRv6
medium Japanese recognition, through RapidOCR on onnxruntime's CUDA provider.

The pair won a 2026-10 benchmark on 44 hand-labelled variety-show frames:
1.5 % character error on content cards, no line missed, and the only
detector whose boxes stay put (1 to 3 px of jitter) on a static card, which the
tracking relies on; about 6 frames/s on an RTX 5090. LLM-backed OCR models
read a little better but cost seconds per frame; the telop skill keeps the
comparison.

The packages are an optional extra (`uv sync --extra telop`) and are
imported on first use; a missing package or CUDA provider fails loudly
rather than falling back to the CPU.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING, Any

from grillmaster.telop.errors import TelopError
from grillmaster.telop.scan import SAMPLE_FPS, TelopScan, TextRead

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# Frames are sampled at exactly this size, so boxes divide by it.
SAMPLE_SIZE = (1920, 1080)
_CUDA = "CUDAExecutionProvider"


# Every text box OCR finds in one `SAMPLE_SIZE` frame.
type OcrEngine = Callable[[Path], list[TextRead]]


def _reads(result: Any) -> list[TextRead]:
    """A RapidOCR result as reads, boxes as fractions of the frame."""
    if not result.txts:
        return []
    width, height = SAMPLE_SIZE
    reads: list[TextRead] = []
    for polygon, text, score in zip(
        result.boxes, result.txts, result.scores, strict=True
    ):
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        box = (min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height)
        reads.append(TextRead(box=box, text=str(text), score=float(score)))
    return reads


_PACKAGES = ("onnxruntime", "rapidocr")


def _missing(name: str) -> TelopError:
    return TelopError(
        f"Telop OCR needs the optional packages ({name} is missing): "
        "run `uv sync --extra telop`"
    )


def require_packages() -> None:
    """`TelopError` unless the optional extra is installed; cheap (nothing
    is imported), for a check before a run starts."""
    for name in _PACKAGES:
        if importlib.util.find_spec(name) is None:
            raise _missing(name)


def gpu_engine() -> OcrEngine:
    """The RapidOCR engine on CUDA; `TelopError` without the extra or CUDA."""
    try:
        onnxruntime, rapidocr = map(importlib.import_module, _PACKAGES)
    except ImportError as error:
        raise _missing(error.name or "?") from error
    onnxruntime.preload_dlls()
    if _CUDA not in onnxruntime.get_available_providers():
        raise TelopError(
            f"onnxruntime has no {_CUDA}; telop OCR runs on the GPU only "
            "(install onnxruntime-gpu through `uv sync --extra telop`)"
        )
    engine = rapidocr.RapidOCR(
        params={
            "Global.use_cls": False,
            "Det.ocr_version": rapidocr.OCRVersion.PPOCRV5,
            "Det.model_type": rapidocr.ModelType.SERVER,
            "Det.lang_type": rapidocr.LangDet.CH,
            "Rec.ocr_version": rapidocr.OCRVersion.PPOCRV6,
            "Rec.model_type": rapidocr.ModelType("medium"),
            "Rec.lang_type": rapidocr.LangRec("japan"),
            "EngineConfig.onnxruntime.use_cuda": True,
            # The default EXHAUSTIVE search re-tunes for every crop shape.
            "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
            # Grow the arena by request, not by doubling: ~5.7 GB instead of ~7.4.
            "EngineConfig.onnxruntime.cuda_ep_cfg.arena_extend_strategy": "kSameAsRequested",
        }
    )
    return lambda image: _reads(engine(str(image)))


def read_frames(
    engine: OcrEngine, frames: Sequence[Path], *, advance: Callable[[], None]
) -> TelopScan:
    """OCR every frame of a `SAMPLE_FPS` sampling, in order."""
    reads: list[list[TextRead]] = []
    for frame in frames:
        reads.append(engine(frame))
        advance()
    return TelopScan(fps=SAMPLE_FPS, frames=reads)
