from __future__ import annotations

import pytest

from grillmaster.agents.errors import (
    AgentAuthError,
    AgentCancelledError,
    AgentConfigError,
    AgentError,
    AgentQuotaError,
    AgentTransientError,
    ValidationFailure,
    classify_failure,
)
from grillmaster.events.types import SessionOutcome


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("RESOURCE_EXHAUSTED: quota exceeded", None, AgentQuotaError),
        ("You've hit your usage limit", None, AgentQuotaError),
        # A quota phrase is a quota whatever the status...
        (
            '{"type":"error","status":429,"error":{"message":"usage limit reached"}}',
            None,
            AgentQuotaError,
        ),
        ("You've hit your session limit", 429, AgentQuotaError),
        ('{"status":400,"error":{"type":"insufficient_quota"}}', None, AgentQuotaError),
        # ...while a bare rate limit is short-lived: retried, never latched.
        ('{"type":"error","status":429}', None, AgentTransientError),
        ("anything", 429, AgentTransientError),
        ("429 Too Many Requests, rate limit, retry later", None, AgentTransientError),
        ("429 Too Many Requests, rate limit, retry later", 429, AgentTransientError),
        ("HTTP 401 Unauthorized", None, AgentAuthError),
        ("not logged in", None, AgentAuthError),
        (
            '{"status":400,"error":{"message":"model not supported"}}',
            None,
            AgentConfigError,
        ),
        # Google's error bodies carry the status as `code`.
        ('[{"error": {"code": 400, "message": "bad"}}]', None, AgentConfigError),
        # A longer number is no HTTP status.
        ('{"error": {"code": 40001, "message": "bad"}}', None, AgentTransientError),
        ("stream disconnected", None, AgentTransientError),
        ("overloaded", 529, AgentTransientError),
        # Whole words only: these merely contain a marker.
        ("stream error: retries exhausted", None, AgentTransientError),
        ("request 4291 failed", None, AgentTransientError),
        ("connection reset after 14010 bytes", None, AgentTransientError),
        ("generate failed: deliberately unquotable", None, AgentTransientError),
        # Separators still count as word edges.
        ("insufficient_quota", None, AgentQuotaError),
        ("rate_limit_error: slow down", None, AgentTransientError),
        ("You are being rate-limited", None, AgentTransientError),
        ("Resource has been exhausted (e.g. check quota).", None, AgentQuotaError),
        ("Error 429: too many requests", None, AgentTransientError),
        ("authentication_error: invalid token", None, AgentAuthError),
        ("Login required.", None, AgentAuthError),
    ],
)
def test_classify_failure(message: str, status: int | None, expected: type[AgentError]):
    error = classify_failure(message, login_hint="run `x login`", status=status)
    assert type(error) is expected


def test_auth_errors_name_the_login_command():
    error = classify_failure("401", login_hint="run `codex login`")
    assert "run `codex login`" in str(error)


def test_each_error_maps_to_its_session_outcome():
    assert AgentQuotaError.outcome is SessionOutcome.QUOTA_ERROR
    assert AgentAuthError.outcome is SessionOutcome.AUTH_ERROR
    assert AgentConfigError.outcome is SessionOutcome.CONFIG_ERROR
    assert AgentTransientError.outcome is SessionOutcome.TRANSIENT_ERROR
    assert AgentCancelledError.outcome is SessionOutcome.CANCELLED


def test_validation_failure_lists_problems_under_the_header():
    failure = ValidationFailure.from_problems("請修正：", ["缺少 index：1", "x"])
    assert str(failure) == "請修正：\n- 缺少 index：1\n- x"
