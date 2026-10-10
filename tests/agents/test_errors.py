from __future__ import annotations

import pytest

from grillmaster.agents.errors import (
    AgentAuthError,
    AgentConfigError,
    AgentError,
    AgentQuotaError,
    AgentTransientError,
    classify_failure,
)
from grillmaster.events.types import SessionOutcome


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("RESOURCE_EXHAUSTED: quota exceeded", None, AgentQuotaError),
        ("You've hit your usage limit", None, AgentQuotaError),
        ('{"type":"error","status":429}', None, AgentQuotaError),
        ("anything", 429, AgentQuotaError),
        ("HTTP 401 Unauthorized", None, AgentAuthError),
        ("not logged in", None, AgentAuthError),
        (
            '{"status":400,"error":{"message":"model not supported"}}',
            None,
            AgentConfigError,
        ),
        ("stream disconnected", None, AgentTransientError),
        ("overloaded", 529, AgentTransientError),
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
