"""Repo-local packaging rules keyed by the source series and channel.

`.packagerc` lives at the working-directory root (next to `projects/`) and is
git-ignored. The download stage only ever appends an empty entry for a program
it has just seen; opting a series or channel into remix packaging is a manual
edit that sets `"remix": true` on its entry.
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from services.package.constants import DEFAULT_NOISE_NAME

PACKAGE_RC_FILE_NAME = ".packagerc"


class PackageRcEntry(BaseModel):
    """Packaging rules for one series or channel name."""

    remix: bool | None = None


class PackageRc(BaseModel):
    """The whole `.packagerc` document."""

    series: dict[str, PackageRcEntry] = Field(default_factory=dict)
    channel: dict[str, PackageRcEntry] = Field(default_factory=dict)

    def forces_remix(
        self, *, series: str | None, channel: str | None
    ) -> bool:
        """Whether either name is opted into remix packaging."""
        entries = [
            self.series.get(series) if series else None,
            self.channel.get(channel) if channel else None,
        ]
        return any(entry is not None and entry.remix for entry in entries)


def package_rc_path() -> Path:
    """Path to `.packagerc`, resolved against the working directory."""
    return Path(PACKAGE_RC_FILE_NAME)


def load_package_rc(path: Path | None = None) -> PackageRc:
    """Read `.packagerc`, degrading to empty rules when it cannot be read."""
    rc_path = path or package_rc_path()
    try:
        return _read_package_rc(rc_path)
    except ValueError as e:
        logger.warning(f"Ignoring {rc_path}: {e}")
        return PackageRc()


def register_package_rc_program(
    *,
    series: str | None,
    channel: str | None,
    path: Path | None = None,
) -> None:
    """Add empty `.packagerc` entries for names that are not listed yet."""
    rc_path = path or package_rc_path()
    try:
        rc = _read_package_rc(rc_path)
    except ValueError as e:
        # Never clobber a file the maintainer is editing by hand.
        logger.warning(f"Not updating {rc_path}: {e}")
        return

    registered: list[str] = []
    if series and series not in rc.series:
        rc.series[series] = PackageRcEntry()
        registered.append(f"series '{series}'")
    if channel and channel not in rc.channel:
        rc.channel[channel] = PackageRcEntry()
        registered.append(f"channel '{channel}'")
    if not registered:
        return

    try:
        rc_path.write_text(
            rc.model_dump_json(indent=2, exclude_none=True) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"Failed to write {rc_path}: {e}")
        return
    logger.info(f"Registered in {rc_path}: {', '.join(registered)}")


def resolve_remix_noise_name(
    *,
    requested: str | None,
    series: str | None,
    channel: str | None,
    path: Path | None = None,
) -> str | None:
    """Pick the noise set for this deliverable, honouring `.packagerc`.

    An explicit request always wins. Otherwise a series or channel marked
    `remix` forces remix packaging with the default noise set — a missing
    `<PACKAGE_PATH>/noise/default` folder then fails the package instead of
    quietly falling back to a plain burn-in.
    """
    if requested is not None:
        return requested
    if not load_package_rc(path).forces_remix(series=series, channel=channel):
        return None
    logger.info(
        f"{PACKAGE_RC_FILE_NAME} forces remix for series={series!r} "
        f"channel={channel!r}; using noise '{DEFAULT_NOISE_NAME}'"
    )
    return DEFAULT_NOISE_NAME


def _read_package_rc(rc_path: Path) -> PackageRc:
    """Parse `.packagerc`, treating an absent file as empty rules."""
    if not rc_path.exists():
        return PackageRc()
    try:
        return PackageRc.model_validate_json(
            rc_path.read_text(encoding="utf-8")
        )
    except (ValidationError, json.JSONDecodeError, OSError) as e:
        raise ValueError(f"invalid packaging rules: {e}") from e
