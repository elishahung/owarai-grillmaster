"""Agent failures, classified by what the caller should do about them.

Only `AgentTransientError` is retried, and only by the runner (in a fresh
session, `AgentTask.attempts` times) unless the run was aborted.
Everything else fails the task at once.
`ValidationFailure` is not an `AgentError`: a task's validator raises it, and
the runner turns it into a repair round in the same session.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar

from grillmaster.events.types import SessionOutcome

if TYPE_CHECKING:
    from collections.abc import Iterable


class AgentError(Exception):
    """Base of every classified agent failure."""

    outcome: ClassVar[SessionOutcome]


class AgentConfigError(AgentError):
    """Misconfiguration: unknown model, missing capability or CLI, bad request."""

    outcome = SessionOutcome.CONFIG_ERROR


class AgentQuotaError(AgentError):
    """Subscription quota or usage limit exhausted; the runner stops using
    that backend for the rest of the run."""

    outcome = SessionOutcome.QUOTA_ERROR


class AgentAuthError(AgentError):
    """The CLI is not logged in (HTTP 401); the message names the login command."""

    outcome = SessionOutcome.AUTH_ERROR


class AgentTransientError(AgentError):
    """Timeout, crash, empty output or a short-lived rate limit (HTTP 429
    without a quota phrase): a fresh session may succeed."""

    outcome = SessionOutcome.TRANSIENT_ERROR


class AgentOutputError(AgentError):
    """The output still failed validation after every repair round."""

    outcome = SessionOutcome.OUTPUT_ERROR


class AgentInputError(AgentError):
    """The model declared an input it must perceive unusable (it cannot hear
    the audio); the turn is stopped at once, never repaired or retried."""

    outcome = SessionOutcome.INPUT_ERROR


class AgentCancelledError(AgentError):
    """Not started, or stopped between turns: the run was aborted. Never
    retried; a re-run resumes the work."""

    outcome = SessionOutcome.CANCELLED


class ValidationFailure(Exception):  # noqa: N818 - a verdict, not a crash
    """Raised by a task validator; the message is sent back as the repair prompt,
    so it must say exactly what is wrong."""

    @classmethod
    def from_problems(cls, header: str, problems: Iterable[str]) -> ValidationFailure:
        """`header`, then one `- <problem>` line per problem."""
        return cls(header + "\n" + "\n".join(f"- {problem}" for problem in problems))


def _phrases(*patterns: str) -> re.Pattern[str]:
    """Any of `patterns` as a whole word: no letter or digit may touch it,
    so "4291" is not "429"; `_` and `-` separate (`insufficient_quota`)."""
    return re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(patterns) + r")(?![a-z0-9])", re.IGNORECASE
    )


_QUOTA_RE = _phrases(
    "quotas?",
    r"resource[ _-]exhausted",
    "resource has been exhausted",
    r"usage[ _-]limits?",
    r"(?:session|weekly|(?:5|five)[ _-]hour)[ _-]limits?",
    "hit your limit",
)
_AUTH_RE = _phrases(
    "401",
    "unauthori[sz]ed",
    "unauthenticated",
    "not logged in",
    "authentication",
    "login required",
)
# OpenAI/Anthropic bodies say `"status": 400`, Google's `"code": 400`.
_STATUS_RE = re.compile(r'"(?:status|code)"\s*:\s*(\d{3})(?!\d)')


def classify_failure(
    message: str, *, login_hint: str, status: int | None = None
) -> AgentError:
    """Turn a CLI error message into the matching `AgentError`.

    Phrases match as whole words ("retries exhausted" and "4291" are not
    quota). A quota phrase (quota, usage limit, resource exhausted) is a
    quota error whatever the status. Otherwise an HTTP status (given, or
    embedded in the message as `"status": 400` or `"code": 400`) decides: 401 is auth, 429 a
    short-lived rate limit (transient), any other 4xx a bad request such as
    an unknown model, so retrying cannot help. Without a status, auth
    phrases decide; rate-limit wording and anything else count as transient.
    `login_hint` is appended to auth failures (e.g. "run `codex login`").
    """
    if _QUOTA_RE.search(message):
        return AgentQuotaError(message)
    if status is None:
        match = _STATUS_RE.search(message)
        status = int(match.group(1)) if match else None
    if status == HTTPStatus.UNAUTHORIZED or (
        status is None and _AUTH_RE.search(message)
    ):
        return AgentAuthError(f"{message} ({login_hint})")
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return AgentTransientError(message)
    if status is not None and (
        HTTPStatus.BAD_REQUEST <= status < HTTPStatus.INTERNAL_SERVER_ERROR
    ):
        return AgentConfigError(message)
    return AgentTransientError(message)
