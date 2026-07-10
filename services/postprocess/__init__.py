"""Optional agent-driven post-processing tasks.

`cover` (image stylization, always Codex), `refine` and `glossary_check`
(subtitle passes, backend chosen by `settings.agent_postprocess_model`), and
`date_research` (broadcast-date web research, `settings.agent_common_model`).
Each task
is a thin orchestrator over `services.inference`; the agent does its work by
reading/writing files in the project directory (or, for date research,
returning schema-validated JSON) and we validate it afterward.
"""

__all__ = [
    "CoverFileMissingError",
    "DateResearchResult",
    "GlossaryCheckError",
    "RefinementValidationError",
    "apply_date_research_result",
    "generate_cover",
    "glossary_check_subtitles",
    "load_cached_date_research",
    "refine_subtitles",
    "research_broadcast_date",
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
    if name in {
        "DateResearchResult",
        "apply_date_research_result",
        "load_cached_date_research",
        "research_broadcast_date",
    }:
        from .date_research import (
            DateResearchResult,
            apply_date_research_result,
            load_cached_date_research,
            research_broadcast_date,
        )

        return {
            "DateResearchResult": DateResearchResult,
            "apply_date_research_result": apply_date_research_result,
            "load_cached_date_research": load_cached_date_research,
            "research_broadcast_date": research_broadcast_date,
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
