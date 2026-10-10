"""Platform → `SourcePlatform`; adding a platform touches only `sources/`.

Parsing what the user typed into a `Platform` is `core.source_id`'s job.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from grillmaster.sources.abema import AbemaPlatform
from grillmaster.sources.bilibili import BilibiliPlatform
from grillmaster.sources.tver import TverPlatform
from grillmaster.sources.youtube import YouTubePlatform

if TYPE_CHECKING:
    from collections.abc import Mapping

    from grillmaster.core.source_id import Platform
    from grillmaster.sources.base import SourcePlatform

PLATFORMS: Mapping[Platform, SourcePlatform] = MappingProxyType(
    {
        implementation.platform: implementation
        for implementation in (
            YouTubePlatform(),
            BilibiliPlatform(),
            TverPlatform(),
            AbemaPlatform(),
        )
    }
)


def source_platform(platform: Platform) -> SourcePlatform:
    return PLATFORMS[platform]
