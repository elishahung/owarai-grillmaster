"""Codex-driven fixed-glossary localization check (runs after refine).

A narrow pass over the refined SRT: Python flags blocks whose text still
carries Latin letters or Japanese kana, hands that short list to Codex with
the curated fixed glossary, and Codex swaps only the genuine glossary misses
into `video.cht.glossary_checked.srt`. A block is not flagged when its only
foreign content is already an exact curated `zh` rendering (a known-good
term, not a miss). The glossary files are materialized into
`.glossary_check/` by Python and removed afterward — the model never copies
or deletes them.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from loguru import logger

from project import Project
from settings import settings
from services.inference import Backend, run_inference
from services.fixed_glossary.fixed_glossary import (
    FIXED_GLOSSARY_PATH,
    load_fixed_glossary,
)
from services.srt import SrtBlock
from ._srt_guard import parse_srt_file, validate_srt_against_source


_PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompts" / "glossary_check.md"
).read_text(encoding="utf-8")

_FIXED_GLOSSARY_MD_PATH = FIXED_GLOSSARY_PATH.with_suffix(".md")

# Flag a block only when its text has a RUN of >=2 consecutive Latin
# letters or >=2 consecutive kana (hiragana U+3041-3096 / katakana
# U+30A1-30FA, plus the U+30FC prolonged-sound mark so words like コーナー
# stay one run). A lone stray letter or single kana is noise, not a
# glossary miss. A block mixing Han with such a run still flags — we are
# not requiring the block to be Han-free.
_SUSPECT_RE = re.compile(r"[A-Za-z]{2,}|[ぁ-ゖァ-ヺー]{2,}")

# Single Latin letter or kana — used to test whether a curated term sits
# inside a larger foreign run (i.e. a partial fragment, which must stay
# flagged rather than be treated as an exact glossary hit).
_FOREIGN_CHAR_RE = re.compile(r"[A-Za-zぁ-ゖァ-ヺー]")


class GlossaryCheckError(RuntimeError):
    """Raised when the glossary-checked SRT is missing or diverges structurally."""


def _glossary_zh_terms() -> list[str]:
    """Curated `zh` renderings that themselves carry Latin/kana, longest first.

    Only foreign-bearing targets can ever cancel a flag; longest-first
    ordering strips a multi-word name (e.g. `Long Coat Daddy`) before any
    shorter entry that is a substring of it.
    """
    glossary = load_fixed_glossary()
    terms: set[str] = set()
    for unit in glossary.talents:
        for _aliases, zh in unit.entries():
            terms.add(zh)
    for _aliases, zh in glossary.others:
        terms.add(zh)
    relevant = [term for term in terms if _SUSPECT_RE.search(term)]
    relevant.sort(key=len, reverse=True)
    return relevant


def _strip_exact_glossary(text: str, terms: list[str]) -> str:
    """Drop whole-token occurrences of curated `zh` terms from `text`.

    An occurrence is removed only when it is NOT flanked by another
    Latin/kana char, which guarantees a complete match rather than a partial
    fragment of a larger foreign token (those must stay flagged).
    """
    for term in terms:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx == -1:
                break
            end = idx + len(term)
            before = text[idx - 1] if idx > 0 else ""
            after = text[end] if end < len(text) else ""
            if (before and _FOREIGN_CHAR_RE.match(before)) or (
                after and _FOREIGN_CHAR_RE.match(after)
            ):
                start = idx + 1  # embedded -> partial fragment, keep it
                continue
            text = text[:idx] + text[end:]
            start = idx
    return text


def _is_suspect(text: str, glossary_terms: list[str]) -> bool:
    if not _SUSPECT_RE.search(text):
        return False
    if not glossary_terms:
        return True
    return (
        _SUSPECT_RE.search(_strip_exact_glossary(text, glossary_terms))
        is not None
    )


def _suspect_blocks(blocks: list[SrtBlock]) -> list[SrtBlock]:
    glossary_terms = _glossary_zh_terms()
    return [
        block for block in blocks if _is_suspect(block.text, glossary_terms)
    ]


def _render_suspect_list(blocks: list[SrtBlock]) -> str:
    return "\n".join(
        f"- #{block.index}: {' '.join(block.text.splitlines()).strip()}"
        for block in blocks
    )


def glossary_check_subtitles(project: Project) -> None:
    """Run the Codex glossary check and structurally validate the output.

    Idempotent on the produced file. When no block carries English/kana the
    Codex call is skipped and no output file is written, so the workflow's
    finalize stage transparently falls back to the refined SRT.
    """
    if project.glossary_checked_srt_path.exists():
        logger.info(
            f"Glossary-checked SRT already exists, skipping Codex invocation: "
            f"{project.glossary_checked_srt_path}"
        )
        return

    if not project.refined_srt_path.exists():
        raise GlossaryCheckError(
            f"refined SRT missing before glossary check: "
            f"{project.refined_srt_path}"
        )

    suspects = _suspect_blocks(parse_srt_file(project.refined_srt_path))
    if not suspects:
        logger.info(
            f"No Latin/kana blocks found; skipping glossary-check Codex "
            f"invocation (finalize falls back to refined SRT): {project.id}"
        )
        return

    project.glossary_check_cache_dir.mkdir(parents=True, exist_ok=True)
    gloss_json_dst = project.glossary_check_cache_dir / FIXED_GLOSSARY_PATH.name
    gloss_md_dst = (
        project.glossary_check_cache_dir / _FIXED_GLOSSARY_MD_PATH.name
    )
    copied: list[Path] = []
    try:
        if not FIXED_GLOSSARY_PATH.exists():
            raise GlossaryCheckError(
                f"fixed glossary json missing: {FIXED_GLOSSARY_PATH}"
            )
        shutil.copyfile(FIXED_GLOSSARY_PATH, gloss_json_dst)
        copied.append(gloss_json_dst)
        if _FIXED_GLOSSARY_MD_PATH.exists():
            shutil.copyfile(_FIXED_GLOSSARY_MD_PATH, gloss_md_dst)
            copied.append(gloss_md_dst)
        else:
            logger.warning(
                f"fixed glossary philosophy md missing, proceeding without "
                f"it: {_FIXED_GLOSSARY_MD_PATH}"
            )

        prompt = (
            _PROMPT_TEMPLATE
            + "\n\nFlagged blocks (only these may be edited):\n"
            + _render_suspect_list(suspects)
            + "\n"
        )
        backend = Backend(settings.agent_postprocess_backend)
        logger.info(
            f"Invoking {backend.value} for glossary check "
            f"({len(suspects)} flagged blocks): {project.id}"
        )
        spec = settings.agent_postprocess_model
        run_inference(
            backend=backend,
            prompt=prompt,
            cwd=project.project_path,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
        )

        if not project.glossary_checked_srt_path.exists():
            raise GlossaryCheckError(
                f"Codex did not produce glossary-checked SRT: "
                f"{project.glossary_checked_srt_path}"
            )

        errors = validate_srt_against_source(
            project.refined_srt_path, project.glossary_checked_srt_path
        )
        if errors:
            raise GlossaryCheckError(
                "glossary-checked SRT failed structural validation:\n"
                + "\n".join(errors)
            )

        logger.info(
            f"Glossary-checked SRT validated: "
            f"{len(parse_srt_file(project.glossary_checked_srt_path))} blocks"
        )

        if not project.glossary_check_report_path.exists():
            logger.info(
                f"Glossary check report absent (no changes, or expected at "
                f"{project.glossary_check_report_path})"
            )
    finally:
        for path in copied:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
