"""Pydantic bases for every persisted, configured or agent-produced schema."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Unknown fields fail validation, and so does a bad assignment: a typo or
    a stale key never passes silently."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FrozenModel(StrictModel):
    """An immutable, hashable `StrictModel`."""

    model_config = ConfigDict(frozen=True)
