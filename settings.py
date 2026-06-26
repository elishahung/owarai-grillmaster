from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ModelSpec(BaseModel):
    """A backend model plus its reasoning effort.

    Written in env/config as ``"model"`` or ``"model/effort"`` (effort is one of
    low/medium/high). A bare ``"gpt-5.5"`` defaults the effort to ``high``;
    ``"gpt-5.5/medium"`` sets it explicitly. The split happens here so call
    sites just read ``.model`` and ``.reasoning_effort``.
    """

    model: str
    reasoning_effort: str = "high"

    def __str__(self) -> str:
        return f"{self.model}/{self.reasoning_effort}"


def _parse_model_spec(value: object) -> object:
    if isinstance(value, str):
        model, sep, effort = value.partition("/")
        parsed: dict[str, str] = {"model": model.strip()}
        if sep and effort.strip():
            parsed["reasoning_effort"] = effort.strip()
        return parsed
    return value


# A ModelSpec field that accepts the "model[/effort]" shorthand string. NoDecode
# stops pydantic-settings from JSON-decoding the env value (it would otherwise
# treat a BaseModel field as complex), so the raw string reaches the validator.
ModelSpecField = Annotated[
    ModelSpec, NoDecode, BeforeValidator(_parse_model_spec)
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validate defaults too, so a bare-string default is parsed into a
        # ModelSpec (defaults are not validated by pydantic otherwise).
        validate_default=True,
    )

    # --- ASR: ElevenLabs Scribe ---------------------------------------------
    elevenlabs_api_key: str | None = Field(
        default=None,
        description="API key for ElevenLabs Speech to Text",
    )
    elevenlabs_stt_model: str = Field(
        default="scribe_v2",
        description="ElevenLabs Speech to Text model identifier",
    )
    elevenlabs_stt_language_code: str = Field(
        default="jpn",
        description="Language code hint for ElevenLabs Speech to Text",
    )

    # Source SRT formatting parameters live as hard-coded constants at
    # the top of services/elevenlabs/srt.py — they are fine-tuned by
    # the maintainer, not exposed as configuration.

    # --- Agent / model backends (shared) ------------------------------------
    # Every model-driven stage (pre-pass, chunk, post-processing) picks one
    # backend: 'gemini-api', 'gemini-cli', 'claude', or 'codex'. gemini-cli /
    # claude / codex use subscription/OAuth auth; gemini-api uses
    # AGENT_GEMINI_API_KEY (only then is the key required). claude / codex
    # cannot ingest audio, so those stages run on frames + SRT only. Each stage
    # sets its own backend + model + reasoning_effort; the model and effort are
    # passed to whichever backend the stage selected (set them to values that
    # backend understands). Each *_model is written as "model" or "model/effort"
    # (effort low/medium/high, default high) and parsed into a ModelSpec; effort
    # is mapped per client (gemini thinking_level, codex model_reasoning_effort,
    # claude effort). The schema validate-and-repair cap is NOT configurable — it
    # is the hardcoded MAX_SCHEMA_RETRIES constant in
    # services/inference/schema_enforce.py.
    agent_gemini_api_key: str | None = Field(
        default=None,
        description="API key for Google Gemini. Required only when a stage uses the 'gemini-api' backend.",
    )

    agent_prepass_backend: str = Field(
        default="gemini-api",
        description="Backend for the pre-pass stage: 'gemini-api', 'gemini-cli', 'claude', or 'codex'.",
    )
    agent_prepass_model: ModelSpecField = Field(
        default="gemini-3-flash-preview",
        description="Pre-pass model as 'model' or 'model/effort' (effort low/medium/high, default high), passed to the selected backend.",
    )

    agent_chunk_backend: str = Field(
        default="gemini-api",
        description="Backend for chunk translation: 'gemini-api', 'gemini-cli', 'claude', or 'codex'.",
    )
    agent_chunk_model: ModelSpecField = Field(
        default="gemini-3-flash-preview",
        description="Chunk model as 'model' or 'model/effort' (effort low/medium/high, default high), passed to the selected backend.",
    )

    agent_postprocess_backend: str = Field(
        default="codex",
        description="Backend for agent-driven post-processing (subtitle refine + glossary_check + chunk structural fix): 'codex' or 'claude'. Cover is always Codex (image generation).",
    )
    agent_postprocess_model: ModelSpecField = Field(
        default="gpt-5.5/medium",
        description="Post-processing model as 'model' or 'model/effort' (effort low/medium/high, default high), passed to the selected backend.",
    )

    video_frame_max_side: int = Field(
        default=768,
        description="Maximum pixel length of the longest side for sampled video frames (pre-pass, chunk, and the on-demand agent frame tool).",
    )

    # --- Translation: media sampling & chunking (backend-agnostic) ----------
    prepass_frame_interval_seconds: int = Field(
        default=120,
        description="Absolute video frame sampling interval in seconds for pre-pass inputs",
    )
    enable_prepass_full_fixed_glossary: bool = Field(
        default=False,
        description="Pre-pass fixed glossary injection mode. False = normalized substring pre-filter (only matched entries injected). True = inject the entire glossary as a reference table and let the model resolve matches.",
    )
    chunk_char_limit: int = Field(
        default=6000,
        description="Target character count per chunk when splitting SRT for concurrent translation (~5 min of variety show subtitles)",
    )
    chunk_api_concurrency: int = Field(
        default=10,
        description="Maximum concurrent chunk requests for the gemini-api backend (cheap network HTTP calls, can fan out widely).",
    )
    chunk_agent_concurrency: int = Field(
        default=5,
        description="Maximum concurrent chunk processes for the agent backends (gemini-cli / codex / claude); lower than chunk_api_concurrency since each spawns a heavy local process.",
    )
    chunk_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts per chunk on translation failure",
    )
    chunk_frame_interval_seconds: int = Field(
        default=30,
        description="Frame budget interval in seconds for SRT-start-based chunk translation frame sampling",
    )
    # --- Download & pipeline extras -----------------------------------------
    cookies_txt_path: Path | None = Field(
        default=None,
        description="Path to cookies.txt file used for downloading content",
    )
    archived_path: Path | None = Field(
        default=None,
        description="Path for automatic archival. If set, completed projects will be archived to this location",
    )
    package_path: Path | None = Field(
        default=None,
        description="Path for final deliverable packaging. If set, after archive, burn ASS subtitles into the video and copy the cover image to <package_path>/<id>_<name>/",
    )

    # --- Optional post-processing toggles -----------------------------------
    enable_postprocess_refine: bool = Field(
        default=False,
        description="Enable optional agent-driven Traditional Chinese subtitle refinement stage between TRANSLATED and FINALIZED",
    )
    enable_postprocess_glossary_check: bool = Field(
        default=False,
        description="Enable optional agent-driven fixed-glossary localization check stage between SRT_REFINED and FINALIZED. Independent of refine; only runs if a refined SRT exists.",
    )
    enable_cover_generation: bool = Field(
        default=False,
        description="Enable optional agent-driven cover image stylization (runs async after DOWNLOADED, joined before archive). Skipped entirely when break_after is set.",
    )


settings = Settings()
