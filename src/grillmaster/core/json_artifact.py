"""Fixed-filename JSON artifacts: atomic writes and miss-on-corrupt reads.

Caches in this project never self-invalidate. A file that exists and parses
is a hit; a missing file is a miss; a corrupt file is also a miss (logged),
so a half-written artifact from a crash is simply redone. `read_model` is the
strict counterpart for inputs whose absence is a bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, ValidationError

from grillmaster.core.fs import atomic_write_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import TypeAdapter


def write_model(path: Path, model: BaseModel) -> None:
    atomic_write_text(path, model.model_dump_json(indent=2) + "\n")


def load_model[M: BaseModel](path: Path, model: type[M]) -> M | None:
    """Parse `path` as `model`; `None` when missing or corrupt."""
    return _load(path, model.model_validate_json)


def load_adapted[T](path: Path, adapter: TypeAdapter[T]) -> T | None:
    """`load_model` for non-model types (lists, unions) via a `TypeAdapter`."""
    return _load(path, adapter.validate_json)


def read_model[M: BaseModel](path: Path, model: type[M]) -> M:
    """Parse `path` as `model`, raising when it is missing or corrupt.

    For inputs that must exist (a tool-session manifest, a finished stage's
    output); caches use `load_model` instead.
    """
    return model.model_validate_json(path.read_text(encoding="utf-8-sig"))


def _load[T](path: Path, parse: Callable[[str], T]) -> T | None:
    try:
        return parse(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (ValidationError, UnicodeDecodeError) as error:
        logger.warning(f"Ignoring unreadable artifact {path}: {error}")
        return None
