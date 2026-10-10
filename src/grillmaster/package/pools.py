"""Media pools: numbered files under `<package>/pools/<name>/` and a cursor.

A pool holds `001.*`, `002.*`, ... (contiguous three-digit stems, any
container) and a `.cursor.json` saying where the next draw starts. Inserts
take whole files in rotation (`next_file`); remix noise walks the files in
seconds (`reserve_seconds`). Both advance the cursor **before** the caller
uses what it drew: concurrent packaging runs must never ship the same media,
so a draw is a commitment and a run that then fails simply skips it. Each
read-modify-write of the cursor holds `.cursor.lock`, so two runs drawing at
once take consecutive media rather than the same.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grillmaster.core.fs import exclusive_lock
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.media import probe
from grillmaster.package.errors import PoolError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from grillmaster.media.ffmpeg import FfmpegRunner

POOLS_DIR_NAME = "pools"
CURSOR_FILE_NAME = ".cursor.json"
CURSOR_LOCK_NAME = ".cursor.lock"
# A draw holds the lock for a few file reads and, for a seconds walk, an
# ffprobe per file it walks; a lock held longer belongs to a crashed run.
CURSOR_LOCK_TIMEOUT_SECONDS = 60.0
_STEM_DIGITS = 3


class _Cursor(BaseModel):
    """Where the next draw starts: a 0-based file position and, for a
    seconds walk, the offset inside that file."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(default=0, ge=0)
    seconds: int = Field(default=0, ge=0)


def require_pools(package_root: Path, names: Iterable[str]) -> None:
    """Fail (`PoolError`) unless every named pool of `package_root` exists and
    is well numbered; packaging checks this before drawing from any pool."""
    for name in names:
        MediaPool.under(package_root, name).files()


@dataclass(frozen=True, slots=True)
class Cut:
    """`duration` seconds of `source` from `start`."""

    source: Path
    start: float
    duration: float


class MediaPool:
    """One pool directory; every draw re-reads the files and the cursor."""

    def __init__(
        self, directory: Path, *, lock_timeout: float = CURSOR_LOCK_TIMEOUT_SECONDS
    ) -> None:
        self.directory = directory
        self._lock_timeout = lock_timeout

    @classmethod
    def under(cls, package_root: Path, name: str) -> MediaPool:
        """Pool `name` of the package root (`<package>/pools/<name>/`)."""
        return cls(package_root / POOLS_DIR_NAME / name)

    @property
    def name(self) -> str:
        return self.directory.name

    def files(self) -> list[Path]:
        """The pool's media in index order; raises `PoolError` when the
        directory is missing, empty, or not numbered `001..N`."""
        if not self.directory.is_dir():
            raise PoolError(f"Media pool not found: {self.directory}")
        files = sorted(
            (
                path
                for path in self.directory.iterdir()
                if path.is_file()
                and path.stem.isdigit()
                and len(path.stem) == _STEM_DIGITS
            ),
            key=lambda path: path.stem,
        )
        if not files:
            raise PoolError(f"Media pool is empty: {self.directory}")
        expected = [f"{index:03d}" for index in range(1, len(files) + 1)]
        if [path.stem for path in files] != expected:
            raise PoolError(
                f"Media pool files must be numbered 001..{len(files):03d} "
                f"without gaps: {self.directory}"
            )
        return files

    def next_file(self) -> Path:
        """The next file in rotation; wraps to the first after the last."""
        files = self.files()
        with self._cursor_lock():
            index = self._read_cursor().index % len(files)
            self._write_cursor(_Cursor(index=(index + 1) % len(files)))
        return files[index]

    def reserve_seconds(
        self, seconds: float, *, count: int, ffmpeg: FfmpegRunner
    ) -> list[Cut]:
        """Reserve `count` consecutive cuts of about `seconds` each.

        The files are walked in index order and wrap back to the first. A
        file whose remainder would be shorter than one more full cut is
        consumed to its end in the current cut (so a cut runs up to twice
        `seconds`, or less when a whole file is shorter). Durations come
        from ffprobe through `ffmpeg`.
        """
        if count <= 0:
            raise ValueError("count must be positive")
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        files = self.files()
        durations: dict[int, float] = {}

        def duration_of(index: int) -> float:
            if index not in durations:
                durations[index] = probe.duration(ffmpeg, files[index])
            return durations[index]

        with self._cursor_lock():
            cursor = self._read_cursor()
            index = cursor.index % len(files)
            start = float(cursor.seconds)
            cuts: list[Cut] = []
            for _ in range(count):
                skipped = 0
                while duration_of(index) - start <= 0:
                    index = (index + 1) % len(files)
                    start = 0.0
                    skipped += 1
                    if skipped > len(files):
                        raise PoolError(f"No usable media in pool: {self.directory}")
                remaining = duration_of(index) - start
                if remaining < 2 * seconds:
                    cuts.append(Cut(files[index], start, remaining))
                    index = (index + 1) % len(files)
                    start = 0.0
                else:
                    cuts.append(Cut(files[index], start, float(seconds)))
                    start += seconds
            self._write_cursor(_Cursor(index=index, seconds=int(start)))
        return cuts

    @property
    def _cursor_path(self) -> Path:
        return self.directory / CURSOR_FILE_NAME

    @contextmanager
    def _cursor_lock(self) -> Iterator[None]:
        with ExitStack() as stack:
            try:
                stack.enter_context(
                    exclusive_lock(
                        self.directory / CURSOR_LOCK_NAME, timeout=self._lock_timeout
                    )
                )
            except TimeoutError as error:
                raise PoolError(f"Media pool cursor is locked: {error}") from error
            yield

    def _read_cursor(self) -> _Cursor:
        if not self._cursor_path.exists():
            return _Cursor()
        try:
            return read_model(self._cursor_path, _Cursor)
        except ValidationError as error:
            raise PoolError(f"Invalid pool cursor: {self._cursor_path}") from error

    def _write_cursor(self, cursor: _Cursor) -> None:
        write_model(self._cursor_path, cursor)
