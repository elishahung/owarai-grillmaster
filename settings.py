from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
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

    # --- Translation: Gemini (shared) --------------------------------------
    # Each translation stage (pre-pass, chunk) independently picks an "api" or
    # "cli" backend below. The CLI backend uses subscription/OAuth auth; the
    # api backend uses GEMINI_API_KEY (only then is the key required).
    gemini_api_key: str | None = Field(
        default=None,
        description="API key for Google Gemini. Required only when a stage's backend is 'api'.",
    )
    gemini_thinking_level: str = Field(
        default="HIGH",
        description="Thinking level for api-backend translation calls. One of: LOW, MEDIUM, HIGH",
    )
    gemini_cli_max_retries: int = Field(
        default=3,
        description="Schema-repair retry cap for the Gemini CLI (each repair is a real CLI quota request). Used by the CLI backend; pre-pass enforces a JSON schema, chunk does not.",
    )

    # --- Translation: pre-pass ---------------------------------------------
    prepass_gemini_backend: str = Field(
        default="api",
        description="Backend for the pre-pass stage: 'api' (paid Gemini API) or 'cli' (gemini CLI, subscription auth)",
    )
    prepass_gemini_model: str = Field(
        default="gemini-3.1-pro-preview",
        description="Model identifier for the pre-pass stage, passed to whichever backend is selected",
    )
    prepass_frame_interval_seconds: int = Field(
        default=120,
        description="Absolute video frame sampling interval in seconds for pre-pass inputs",
    )
    prepass_frame_max_side: int = Field(
        default=768,
        description="Maximum pixel length of the longest side for pre-pass frame images",
    )
    enable_full_fixed_glossary: bool = Field(
        default=False,
        description="Pre-pass fixed glossary injection mode. False = normalized substring pre-filter (only matched entries injected). True = inject the entire glossary as a reference table and let the model resolve matches.",
    )

    # --- Translation: chunk ------------------------------------------------
    chunk_gemini_backend: str = Field(
        default="api",
        description="Backend for chunk translation: 'api' (paid Gemini API) or 'cli' (gemini CLI, subscription auth)",
    )
    chunk_gemini_model: str = Field(
        default="gemini-3-flash-preview",
        description="Model identifier for chunk translation, passed to whichever backend is selected",
    )
    chunk_char_limit: int = Field(
        default=6000,
        description="Target character count per chunk when splitting SRT for concurrent translation (~5 min of variety show subtitles)",
    )
    chunk_concurrency: int = Field(
        default=10,
        description="Maximum number of concurrent chunk translation requests",
    )
    chunk_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts per chunk on translation failure",
    )
    chunk_frame_interval_seconds: int = Field(
        default=30,
        description="Absolute video frame sampling interval in seconds for chunk translation inputs",
    )
    chunk_frame_max_side: int = Field(
        default=768,
        description="Maximum pixel length of the longest side for chunk frame images",
    )
    chunk_missing_block_tolerance: int = Field(
        default=2,
        description="Maximum number of unmatched/missing subtitle blocks allowed per translated chunk before structural validation fails",
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

    # --- Optional agent-driven post-processing ------------------------------
    enable_srt_refine: bool = Field(
        default=False,
        description="Enable optional agent-driven Traditional Chinese subtitle refinement stage between TRANSLATED and FINALIZED",
    )
    enable_glossary_check: bool = Field(
        default=False,
        description="Enable optional agent-driven fixed-glossary localization check stage between SRT_REFINED and FINALIZED. Independent of ENABLE_SRT_REFINE; only runs if a refined SRT exists.",
    )
    enable_cover_generation: bool = Field(
        default=False,
        description="Enable optional agent-driven cover image stylization (runs async after DOWNLOADED, joined before archive). Skipped entirely when break_after is set.",
    )
    agent_backend: str = Field(
        default="codex",
        description="Agent backend for agent-driven tasks (subtitle refine + glossary_check + chunk structural fix): 'codex' or 'claude'. Cover is always Codex (image generation).",
    )
    claude_model: str = Field(
        default="claude-opus-4-8",
        description="Model used by the Claude Agent SDK backend",
    )


settings = Settings()
