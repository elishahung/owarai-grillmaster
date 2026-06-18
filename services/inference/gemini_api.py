"""Gemini genai-SDK backend: native schema, audio + image input, metered cost.

The genai SDK enforces a response schema natively (``response_json_schema``),
so no validate-and-repair loop is needed here. This is the only metered
backend; cost is computed from the response's token usage. The client is built
lazily and only when this backend is actually used, so a run that never selects
``gemini-api`` needs no ``AGENT_GEMINI_API_KEY``.
"""

from __future__ import annotations

from pathlib import Path

from google import genai
from loguru import logger
from pydantic import BaseModel

from settings import settings
from .base import DEFAULT_TIMEOUT_SECS, InferenceError
from .result import InferenceResult


class GeminiApiError(InferenceError):
    """Raised when the genai SDK call fails or returns an unusable response."""


_client: genai.Client | None = None


def _api_client() -> genai.Client:
    """Build (once) and return the genai client, requiring the API key."""
    global _client
    if _client is None:
        if not settings.agent_gemini_api_key:
            raise GeminiApiError(
                "AGENT_GEMINI_API_KEY is required for the gemini-api backend "
                "(set it, or switch the stage backend to gemini-cli)"
            )
        logger.debug("Initializing Gemini API client")
        _client = genai.Client(api_key=settings.agent_gemini_api_key)
    return _client


class _ModelCost(BaseModel):
    input: float
    cache_hit: float
    output: float


# Per-1M-token pricing (USD). Maintainer-curated.
_PRICING: dict[str, _ModelCost] = {
    "gemini-3.1-flash-lite-preview": _ModelCost(
        input=0.25, cache_hit=0.025, output=1.50
    ),
    "gemini-3-flash-preview": _ModelCost(
        input=0.50, cache_hit=0.10, output=3.00
    ),
    "gemini-3.1-pro-preview": _ModelCost(
        input=2.00, cache_hit=0.20, output=12.00
    ),
}


def calculate_cost(
    usage_metadata: "genai.types.GenerateContentResponseUsageMetadata | None",
    model_name: str,
) -> float:
    """Compute USD cost from a response's token usage."""
    if usage_metadata is None:
        logger.warning("Usage metadata is None")
        return 0.0
    if model_name not in _PRICING:
        logger.warning(f"Unknown model: {model_name}")
        return 0.0

    p = _PRICING[model_name]
    total_prompt = usage_metadata.prompt_token_count or 0
    cached_tokens = usage_metadata.cached_content_token_count or 0
    output_tokens = usage_metadata.candidates_token_count or 0
    thinking_tokens = usage_metadata.thoughts_token_count or 0

    # prompt_token_count includes cached tokens; subtract for actual input.
    actual_input_tokens = total_prompt - cached_tokens

    # Thinking tokens are priced the same as output tokens.
    cost_input = (actual_input_tokens / 1_000_000) * p.input
    cost_cache = (cached_tokens / 1_000_000) * p.cache_hit
    cost_output = (output_tokens / 1_000_000) * p.output
    cost_thinking = (thinking_tokens / 1_000_000) * p.output
    total_cost = cost_input + cost_cache + cost_output + cost_thinking

    logger.info(f"--- Cost breakdown ({model_name}) ---")
    logger.info(f"New input tokens: {actual_input_tokens} (${cost_input:.6f})")
    logger.info(f"Cache hit tokens: {cached_tokens} (${cost_cache:.6f})")
    logger.info(f"Output tokens: {output_tokens} (${cost_output:.6f})")
    logger.info(f"Thinking tokens: {thinking_tokens} (${cost_thinking:.6f})")
    logger.info(f"Total cost: ${total_cost:.6f} USD")
    return total_cost


_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_AUDIO_MIME = {
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}


def _mime_for(path: Path, table: dict[str, str]) -> str:
    mime = table.get(path.suffix.lower())
    if mime is None:
        raise GeminiApiError(f"unsupported media type for gemini-api: {path}")
    return mime


def _part_from_path(path: Path, mime: str) -> "genai.types.Part":
    if not path.exists():
        raise FileNotFoundError(f"gemini media file not found: {path}")
    return genai.types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)


def _safety_off() -> list:
    return [
        genai.types.SafetySetting(
            category=cat,
            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
        )
        for cat in (
            genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        )
    ]


def run_gemini_api(
    *,
    prompt: str,
    system_prompt: str | None = None,
    images: list[Path] | None = None,
    audio: list[Path] | None = None,
    schema: type[BaseModel] | None = None,
    model: str,
    reasoning_effort: str = "high",
    timeout: int | None = None,
) -> InferenceResult:
    """One genai SDK generation. Native schema enforcement, metered cost."""
    client = _api_client()
    thinking_level = genai.types.ThinkingLevel[reasoning_effort.upper()]
    # The genai SDK takes its per-request timeout in milliseconds; the rest of
    # the inference layer speaks seconds, so convert here.
    timeout_secs = timeout or DEFAULT_TIMEOUT_SECS
    config_kwargs = dict(
        system_instruction=system_prompt,
        safety_settings=_safety_off(),
        thinking_config=genai.types.ThinkingConfig(
            thinking_level=thinking_level
        ),
        http_options=genai.types.HttpOptions(timeout=timeout_secs * 1000),
    )
    if schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = schema.model_json_schema()
    config = genai.types.GenerateContentConfig(**config_kwargs)

    # Order matches the historical pipeline: audio, then frames, then text.
    parts: list[genai.types.Part] = []
    for a in audio or []:
        parts.append(_part_from_path(a, _mime_for(a, _AUDIO_MIME)))
    for img in images or []:
        parts.append(_part_from_path(img, _mime_for(img, _IMAGE_MIME)))

    response = client.models.generate_content(
        model=model,
        contents=[*parts, prompt],
        config=config,
    )
    finish_reason = (
        response.candidates[0].finish_reason if response.candidates else None
    )
    if (
        finish_reason is not None
        and finish_reason != genai.types.FinishReason.STOP
    ):
        raise GeminiApiError(
            f"Non-STOP finish reason: {finish_reason} (likely MAX_TOKENS)"
        )
    cost = calculate_cost(response.usage_metadata, model)
    text = response.text or ""
    if schema is not None:
        # Native enforcement already guarantees JSON; validate as a guard so a
        # malformed response surfaces here rather than in the caller.
        schema.model_validate_json(text)
    return InferenceResult(text=text, cost=cost, requests=1)
