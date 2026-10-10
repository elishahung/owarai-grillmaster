"""Cover side task: once the download produced `poster.jpg`, the image agent
draws the root `cover.png`, working in `work/side/cover/`.

Runs when `--cover` or `[features] cover` asks for it; the record in
`state.side_tasks.cover` marks it done. An existing `cover.png` is kept and
recorded without an agent call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.extras.cover import generate_cover
from grillmaster.pipeline.side_tasks import SideTaskDef
from grillmaster.project.layout import session_dir

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.pipeline.stage import RunOptions, StageContext


def _run(ctx: StageContext) -> str:
    generate_cover(
        ctx.agents,
        poster=ctx.layout.poster,
        cover=ctx.layout.cover,
        workdir=ctx.workdir,
        session_dir=session_dir(ctx.workdir),
    )
    return ctx.layout.cover.name


def _enabled(options: RunOptions, config: AppConfig) -> bool:
    return options.cover or config.features.cover


def _params(config: AppConfig) -> dict[str, str]:
    return {"model": str(config.agents.roles.spec(Role.IMAGE))}


TASK: SideTaskDef[str] = SideTaskDef(
    key=SideTaskKey.COVER,
    label="Cover generation",
    weight=1,
    start_after=StageKey.DOWNLOAD,
    run=_run,
    enabled=_enabled,
    params=_params,
)
