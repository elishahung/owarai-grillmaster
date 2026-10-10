"""Package delivery step: the finished project as a deliverable folder under
`[paths] package`, burned-in (or remixed) and ready to upload.

Runs after the last stage of every complete run, and alone via `grill
package`; in a run it reads the project where it lives after the archive
move (the archived copy when `[paths] archive` is set). Title
suggestions are generated here (cached in `work/package/titles.json`), the
chat panel is rendered to `work/package/chat.ass`. A remix comes from
`--remix [pool]` or a program rule (`remix = true`, using `package.remix_pool`);
inserts follow `[[package.inserts]]` and the program's `inserts`. Pools are
checked and the remix segments picked before any pool cursor moves; the
folder is built under a staging name and replaces the previous deliverable
only on success; a locked previous deliverable fails before the render
(rename probe). `preflight` checks the package root and the pools before
any stage runs. A project without a briefing packages without its data.
Any failure fails the run; the archive move already happened, so the
resume command is `grill package <archived dir>`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.fs import check_replaceable
from grillmaster.core.json_artifact import read_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import read_srt_file
from grillmaster.extras.cover import copy_cover
from grillmaster.extras.titles import ensure_titles
from grillmaster.package.assemble import (
    PACKAGE_INNER_PATH_RESERVE,
    build_burn_plan,
    copy_reports,
    deliverable_dir,
    require_inputs,
    write_info,
)
from grillmaster.package.errors import PackageError
from grillmaster.package.inserts import Insert, copy_inserts
from grillmaster.package.pools import MediaPool, require_pools
from grillmaster.package.remix import plan_remix, render_remix
from grillmaster.package.render import burn_in
from grillmaster.project.naming import package_destination
from grillmaster.project.state import SourceInfo
from grillmaster.stages._common import program_rules
from grillmaster.stages.base import DeliveryStepDef

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.config.programs import ProgramRules
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import RunOptions, StageContext

KEY = "package"
# The plain burn-in's output; a remix writes `1.mp4`, `2.mp4`, ...
VIDEO_NAME = "video.mp4"


def _run(ctx: StageContext) -> str:
    package_root = _package_root(ctx.config)
    layout = ctx.layout
    require_inputs(layout.video, layout.cht_ass, layout.cht_srt)
    pool, inserts = _pools(ctx.options, ctx.config, ctx.state.source)
    # Whatever can refuse the package runs before a pool cursor moves.
    require_pools(package_root, _pool_names(pool, inserts))
    destination = package_destination(
        ctx.state, package_root, reserve=PACKAGE_INNER_PATH_RESERVE
    )
    check_replaceable(destination)
    remix = (
        None
        if pool is None
        else (
            MediaPool.under(package_root, pool),
            plan_remix(
                ctx.ffmpeg,
                video=layout.video,
                subtitles=[block.time_range for block in read_srt_file(layout.cht_srt)],
            ),
        )
    )
    briefing = _briefing(layout)
    titles = ensure_titles(
        ctx.agents,
        briefing=layout.effective_briefing(),
        cache=layout.titles,
        session_dir=ctx.session_dir(),
        enabled=ctx.config.features.title_suggestion,
    )
    burn = build_burn_plan(
        ctx.ffmpeg,
        video=layout.video,
        dialogue=layout.cht_ass,
        chat=layout.chat_cht_json,
        chat_ass=layout.chat_panel_ass,
        layout=ctx.options.chat_layout,
    )
    with deliverable_dir(destination) as target:
        copy_cover(cover=layout.cover, poster=layout.poster, target_dir=target)
        write_info(
            target,
            titles=titles.model_dump() if titles is not None else None,
            briefing=briefing,
        )
        copy_reports(
            target,
            {
                "refine.md": layout.refine_report,
                "glossary_check.md": layout.glossary_report,
            },
        )
        copy_inserts(inserts, package_root=package_root, target_dir=target)
        if remix is None:
            burn_in(
                ctx.ffmpeg, layout.video, burn, target / VIDEO_NAME, events=ctx.events
            )
        else:
            noise, plan = remix
            render_remix(
                ctx.ffmpeg,
                video=layout.video,
                burn=burn,
                plan=plan,
                noise=noise,
                target_dir=target,
                events=ctx.events,
            )
    logger.success(f"Project packaged to {destination}")
    return str(destination)


def _preflight(
    options: RunOptions, config: AppConfig, state: ProjectState | None
) -> None:
    """Fail before any stage runs when the package root or a pool it will
    draw from is missing. Before the first run the program (series,
    channel) is unknown, so only the `--remix` pool and the inserts no
    program scopes are checked; `_run` checks again with the metadata."""
    package_root = _package_root(config)
    if not package_root.is_dir():
        raise PackageError(f"[paths] package {package_root} is not a directory")
    source = state.source if state is not None else SourceInfo()
    require_pools(package_root, _pool_names(*_pools(options, config, source)))


def _package_root(config: AppConfig) -> Path:
    package_root = config.paths.package
    if package_root is None:
        raise PackageError("[paths] package is not set in grill.toml")
    return package_root


def _briefing(layout: ProjectLayout) -> Briefing | None:
    """The effective briefing; projects migrated without one package
    without its data (a warning, not a failure)."""
    path = layout.effective_briefing()
    if not path.exists():
        logger.warning(f"No briefing at {path}; info.json carries only the titles")
        return None
    return read_model(path, Briefing)


def _pools(
    options: RunOptions, config: AppConfig, source: SourceInfo
) -> tuple[str | None, list[Insert]]:
    """The remix noise pool (`None` for a plain burn-in) and the inserts of
    this deliverable, from the run flags and the program's rules."""
    rules = program_rules(config, source)
    pool = _remix_pool(options, config, rules, source)
    inserts = [
        Insert(pool=rule.pool, output=rule.output)
        for rule in rules.inserts_for(remix=pool is not None)
    ]
    return pool, inserts


def _pool_names(pool: str | None, inserts: Sequence[Insert]) -> list[str]:
    return [insert.pool for insert in inserts] + ([pool] if pool is not None else [])


def _remix_pool(
    options: RunOptions, config: AppConfig, rules: ProgramRules, source: SourceInfo
) -> str | None:
    """The noise pool of a remix deliverable, `None` for a plain burn-in.

    `--remix` wins; otherwise a program marked `remix` forces one with the
    default pool (whose absence then fails the package rather than
    degrading to a burn-in).
    """
    if options.remix is not None:
        return options.remix
    if not rules.remix:
        return None
    pool = config.package.remix_pool
    logger.info(
        f"Program rules force a remix for series={source.series!r} "
        f"channel={source.channel!r}; using pool {pool!r}"
    )
    return pool


def _workdir(layout: ProjectLayout) -> Path:
    return layout.package_work_dir


def _enabled(_options: RunOptions, config: AppConfig) -> bool:
    return config.paths.package is not None


def _params(config: AppConfig) -> dict[str, str]:
    if not config.features.title_suggestion:
        return {}
    return {"titles": str(config.agents.roles.spec(Role.UTILITY))}


STEP = DeliveryStepDef(
    key=KEY,
    label="Package deliverable",
    weight=4,
    run=_run,
    workdir=_workdir,
    enabled=_enabled,
    params=_params,
    preflight=_preflight,
)
