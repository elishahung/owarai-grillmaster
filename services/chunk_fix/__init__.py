"""Chunk SRT structural validation and repair.

Owns the definition of "is this chunk output structurally valid" and the logic
to repair it when it is not. The translation pipeline (`services.gemini`)
depends on this service — never the other way around — so the dependency stays
one-directional: gemini produces chunk translations, then asks this service to
validate and, if needed, repair their structure.

Repair is layered: a cheap in-process fast-path (`canonicalize_by_position`)
handles plain index/timecode drift, and anything harder is handed to an agent
(`fix_chunk_structure`, via `services.agent_exec`) that self-validates with the
`validate_chunk.py` CLI until the output matches the source skeleton.

Exports are lazy so importing the light-weight validation module (e.g. from the
standalone `validate_chunk.py` CLI subprocess) never drags in `settings` or the
agent backends.
"""

__all__ = [
    "ChunkFixError",
    "canonicalize_by_position",
    "fix_chunk_structure",
    "validate_chunk_structure",
]


def __getattr__(name: str):
    if name in {"canonicalize_by_position", "validate_chunk_structure"}:
        from .validation import (
            canonicalize_by_position,
            validate_chunk_structure,
        )

        return {
            "canonicalize_by_position": canonicalize_by_position,
            "validate_chunk_structure": validate_chunk_structure,
        }[name]
    if name in {"ChunkFixError", "fix_chunk_structure"}:
        from .fix import ChunkFixError, fix_chunk_structure

        return {
            "ChunkFixError": ChunkFixError,
            "fix_chunk_structure": fix_chunk_structure,
        }[name]
    raise AttributeError(name)
