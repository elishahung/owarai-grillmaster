"""Meta's Muse API: one video question per request, priced from its usage.

Muse runs behind an OpenAI-compatible Responses API (`API_BASE_URL`, the
`openai` SDK as in Meta's own examples). The clip goes inline as an
`input_video` base64 data URL, read with its sound (inline media is capped
at 50 MB). Muse always reasons and the reasoning shares `max_output_tokens`
with the answer, so the budget is generous: a request that runs out answers
empty with status `incomplete`. `store=False` keeps the request off Meta's
response store.

The key is `META_API_KEY` in the `.env` the manifest names, read from that
file alone: gemini's CLI strips every variable named like a key from its MCP
servers, so a key only in the environment works on some callers and not
others.

`openai` and `dotenv` are imported on use: every tool server and
the grill process import this module, and few of them ever call Meta.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Protocol, cast

from grillmaster.core.models import FrozenModel

if TYPE_CHECKING:
    from pathlib import Path

    import openai
    from openai.types.responses import ResponseInputParam
    from openai.types.shared import ReasoningEffort

    from grillmaster.core.tool_session import MuseEffort, MuseModel

API_BASE_URL = "https://api.meta.ai/v1"
API_KEY_ENV = "META_API_KEY"
# Reasoning plus answer; a 62 s clip at `high` used ~5.5k.
MAX_OUTPUT_TOKENS = 32_000
# Retries of a rate-limited, failed or dropped request, with the SDK's backoff;
# the call's `timeout_s` is shared out among the attempts.
MAX_RETRIES = 1


@dataclass(frozen=True)
class Price:
    """USD per million tokens; output includes reasoning."""

    input: float
    cached_input: float
    output: float


# dev.meta.ai/docs/pricing-rate-limits (2026-10-21); one per `MuseModel`.
PRICES: dict[MuseModel, Price] = {
    "muse-spark-1.3-contributor": Price(input=0.10, cached_input=0.002, output=0.20),
    "muse-spark-1.3": Price(input=1.25, cached_input=0.15, output=4.25),
}


class Usage(FrozenModel):
    """Token counts of one response; `input_tokens` includes the cached ones."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int

    def cost_usd(self, price: Price) -> float:
        uncached = self.input_tokens - self.cached_input_tokens
        return (
            uncached * price.input
            + self.cached_input_tokens * price.cached_input
            + self.output_tokens * price.output
        ) / 1_000_000


@dataclass(frozen=True)
class Answer:
    text: str
    usage: Usage


class MetaError(Exception):
    """A request Meta refused, failed or left without an answer; `usage`
    is set when Meta billed it anyway."""

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class VideoModel(Protocol):
    """The part of `MetaVideoModel` the `watch_clip` tool uses."""

    def ask(self, instructions: str, video: Path) -> Answer: ...


class MetaVideoModel:
    """`model` at `effort`; one `ask`, its retry included, takes at most
    about `timeout_s`. The client is built on the first request."""

    def __init__(
        self, model: MuseModel, effort: MuseEffort, api_key: str, timeout_s: float
    ):
        self._model = model
        self._effort = effort
        self._api_key = api_key
        self._timeout_s = timeout_s

    @cached_property
    def _client(self) -> openai.OpenAI:
        import openai  # noqa: PLC0415 - slow import, see the module docstring

        return openai.OpenAI(
            api_key=self._api_key,
            base_url=API_BASE_URL,
            timeout=self._timeout_s / (MAX_RETRIES + 1),
            max_retries=MAX_RETRIES,
        )

    def ask(self, instructions: str, video: Path) -> Answer:
        import openai  # noqa: PLC0415 - slow import, see the module docstring

        # `input_video` is Meta's content type; the SDK's types lack it.
        content = cast(
            "ResponseInputParam",
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instructions},
                        {
                            "type": "input_video",
                            "video_url": "data:video/mp4;base64,"
                            + base64.b64encode(video.read_bytes()).decode("ascii"),
                        },
                    ],
                }
            ],
        )
        try:
            response = self._client.responses.create(
                model=self._model,
                input=content,
                # Muse's efforts are its own; the SDK's type lists OpenAI's.
                reasoning={"effort": cast("ReasoningEffort", self._effort)},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except openai.APIError as error:
            raise MetaError(f"{type(error).__name__}: {error}") from error
        usage = response.usage
        if usage is None:
            raise MetaError(f"response {response.id} carries no usage")
        answer = Answer(
            text=response.output_text.strip(),
            usage=Usage(
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.input_tokens_details.cached_tokens or 0,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.output_tokens_details.reasoning_tokens or 0,
            ),
        )
        if response.status != "completed" or not answer.text:
            details = response.incomplete_details
            reason = details.reason if details is not None else None
            raise MetaError(
                f"response {response.id} ended {response.status}"
                f"{f' ({reason})' if reason else ''} without an answer",
                usage=answer.usage,
            )
        return answer


def read_api_key(env_file: Path) -> str:
    """`META_API_KEY` from `env_file` alone, never the environment."""
    from dotenv import dotenv_values  # noqa: PLC0415 - imported on use

    key = dotenv_values(env_file, encoding="utf-8").get(API_KEY_ENV)
    if not key:
        raise MetaError(f"{API_KEY_ENV} is not set in {env_file}")
    return key
