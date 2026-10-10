"""Agent failures, classified by what the caller should do about them.

Only `AgentTransientError` is retried, and only by the runner (in a fresh
session, `AgentTask.attempts` times). Everything else fails the task at once.
`ValidationFailure` is not an `AgentError`: a task's validator raises it, and
the runner turns it into a repair round in the same session.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import ClassVar

from grillmaster.events.types import SessionOutcome


class AgentError(Exception):
    """Base of every classified agent failure."""

    outcome: ClassVar[SessionOutcome]


class AgentConfigError(AgentError):
    """Misconfiguration: unknown model, missing capability or CLI, bad request."""

    outcome = SessionOutcome.CONFIG_ERROR


class AgentQuotaError(AgentError):
    """Subscription quota or rate limit reached (HTTP 429)."""

    outcome = SessionOutcome.QUOTA_ERROR


class AgentAuthError(AgentError):
    """The CLI is not logged in (HTTP 401); the message names the login command."""

    outcome = SessionOutcome.AUTH_ERROR


class AgentTransientError(AgentError):
    """Timeout, crash or empty output: a fresh session may succeed."""

    outcome = SessionOutcome.TRANSIENT_ERROR


class AgentOutputError(AgentError):
    """The output still failed validation after every repair round."""

    outcome = SessionOutcome.OUTPUT_ERROR


class ValidationFailure(Exception):  # noqa: N818 - a verdict, not a crash
    """Raised by a task validator; the message is sent back as the repair prompt,
    so it must say exactly what is wrong."""


_QUOTA_MARKERS = (
    "429",
    "quota",
    "exhausted",
    "rate limit",
    "rate_limit",
    "usage limit",
)
_AUTH_MARKERS = (
    "401",
    "unauthorized",
    "unauthenticated",
    "not logged in",
    "authentication",
    "login required",
)
_STATUS_RE = re.compile(r'"status"\s*:\s*(\d{3})')


def classify_failure(
    message: str, *, login_hint: str, status: int | None = None
) -> AgentError:
    """Turn a CLI error message into the matching `AgentError`.

    An HTTP status (given, or embedded in the message as `"status": 400`)
    decides first: 4xx other than 401/429 is a bad request such as an unknown
    model, so retrying cannot help. Otherwise well-known quota and auth
    phrases decide; anything else counts as transient. `login_hint` is
    appended to auth failures (e.g. "run `codex login`").
    """
    if status is None:
        match = _STATUS_RE.search(message)
        status = int(match.group(1)) if match else None
    lowered = message.lower()
    if status == HTTPStatus.TOO_MANY_REQUESTS or (
        status is None and _has(lowered, _QUOTA_MARKERS)
    ):
        return AgentQuotaError(message)
    if status == HTTPStatus.UNAUTHORIZED or (
        status is None and _has(lowered, _AUTH_MARKERS)
    ):
        return AgentAuthError(f"{message} ({login_hint})")
    if status is not None and (
        HTTPStatus.BAD_REQUEST <= status < HTTPStatus.INTERNAL_SERVER_ERROR
    ):
        return AgentConfigError(message)
    return AgentTransientError(message)


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
