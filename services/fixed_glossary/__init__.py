"""Hand-curated jp-aliases→zh fixed glossary (standalone service)."""

from .fixed_glossary import (
    FixedGlossary,
    FixedGlossaryEntry,
    TalentUnit,
    format_fixed_glossary_block,
    load_fixed_glossary,
)

__all__ = [
    "FixedGlossary",
    "FixedGlossaryEntry",
    "TalentUnit",
    "format_fixed_glossary_block",
    "load_fixed_glossary",
]
