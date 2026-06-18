"""Optional agent-driven post-processing tasks.

`cover` (image stylization, always Codex), `refine` and `glossary_check`
(subtitle passes, backend chosen by `settings.agent_backend`). Each task is
a thin orchestrator over `services.agent_exec`; the agent does its work by
reading/writing files in the project directory and we validate them afterward.
"""

__all__ = [
    "CoverFileMissingError",
    "GlossaryCheckError",
    "RefinementValidationError",
    "generate_cover",
    "glossary_check_subtitles",
    "refine_subtitles",
]


def __getattr__(name: str):
    if name in {"RefinementValidationError", "refine_subtitles"}:
        from .refine import RefinementValidationError, refine_subtitles

        return {
            "RefinementValidationError": RefinementValidationError,
            "refine_subtitles": refine_subtitles,
        }[name]
    if name in {"CoverFileMissingError", "generate_cover"}:
        from .cover import CoverFileMissingError, generate_cover

        return {
            "CoverFileMissingError": CoverFileMissingError,
            "generate_cover": generate_cover,
        }[name]
    if name in {"GlossaryCheckError", "glossary_check_subtitles"}:
        from .glossary_check import (
            GlossaryCheckError,
            glossary_check_subtitles,
        )

        return {
            "GlossaryCheckError": GlossaryCheckError,
            "glossary_check_subtitles": glossary_check_subtitles,
        }[name]
    raise AttributeError(name)
