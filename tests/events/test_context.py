from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from grillmaster.events.context import (
    current_stage,
    current_task,
    install_log_context,
    patch_record,
    stage_scope,
    task_scope,
)

if TYPE_CHECKING:
    from loguru import Record


def test_scopes_nest_and_restore():
    assert current_stage() is None
    with stage_scope("chunks"):
        assert current_stage() == "chunks"
        with task_scope("chunks/0001-0119"):
            assert current_task() == "chunks/0001-0119"
            with stage_scope("refine"):
                assert current_stage() == "refine"
            assert current_stage() == "chunks"
        assert current_task() is None
    assert current_stage() is None


def test_scope_is_restored_after_an_exception():
    with pytest.raises(RuntimeError), stage_scope("asr"):
        raise RuntimeError
    assert current_stage() is None


def test_copy_context_carries_scopes_into_pool_workers():
    def read() -> tuple[str | None, str | None]:
        return current_stage(), current_task()

    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        stage_scope("chunks"),
        task_scope("chunks/0120-0240"),
    ):
        carried = pool.submit(contextvars.copy_context().run, read).result()
        bare = pool.submit(read).result()
    assert carried == ("chunks", "chunks/0120-0240")
    # Without copy_context the worker thread sees the defaults.
    assert bare == (None, None)


def test_worker_scopes_do_not_leak_back():
    def enter_task() -> str | None:
        with task_scope("inner"):
            pass
        return current_task()

    with stage_scope("glossary"), ThreadPoolExecutor(max_workers=1) as pool:
        context = contextvars.copy_context()
        assert pool.submit(context.run, enter_task).result() is None
        assert current_task() is None


def _scopes(records: list[Record]) -> list[dict[str, object]]:
    return [dict(record["extra"]) for record in records]


def test_patch_record_writes_scopes_into_extra(log_records: list[Record]):
    patched = logger.patch(patch_record)
    patched.info("outside")
    with stage_scope("prepass"), task_scope("prepass"):
        patched.info("inside")
    assert _scopes(log_records) == [
        {"stage": None, "task": None},
        {"stage": "prepass", "task": "prepass"},
    ]


def test_install_log_context_patches_the_global_logger(log_records: list[Record]):
    install_log_context()
    try:
        with stage_scope("finalize"):
            logger.info("hello")
    finally:
        logger.configure(patcher=lambda _record: None)
    assert _scopes(log_records) == [{"stage": "finalize", "task": None}]
