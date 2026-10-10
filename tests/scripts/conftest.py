from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _load_script(name: str) -> ModuleType:
    """Import `scripts/<name>.py`, which is not part of any package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: dataclasses resolve annotations through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def migrate_archive() -> ModuleType:
    return _load_script("migrate_archive")


@pytest.fixture(scope="session")
def migrate_config() -> ModuleType:
    return _load_script("migrate_config")
