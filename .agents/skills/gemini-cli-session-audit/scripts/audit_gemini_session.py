#!/usr/bin/env python
"""Audit Gemini CLI session JSONL files without confusing prompts for tools."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


FRAME_COMMAND_RE = re.compile(
    r"get_frames_for_(?P<stage>pre_pass|chunk|refine)\.py.*?"
    r"--project-dir\s+[\"'](?P<project>[^\"']+)[\"']\s+"
    r"--times\s+[\"'](?P<times>[^\"']+)[\"']",
    re.IGNORECASE | re.DOTALL,
)
FRAME_PATH_RE = re.compile(
    r"frame_\d+\.\d{3}_\d+\.jpg",
    re.IGNORECASE,
)
SRT_START_RE = re.compile(r"^\d+\n\d\d:\d\d:\d\d,\d{3}\s+-->\s+", re.MULTILINE)


def short(value: Any, limit: int = 500) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\r", "").replace("\n", "\\n")
    return text[:limit] + ("..." if len(text) > limit else "")


def session_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    chats = path / "chats"
    if chats.is_dir():
        return sorted(chats.glob("session-*.jsonl"))
    return sorted(path.glob("session-*.jsonl"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            if isinstance(record, dict):
                record["_line_no"] = line_no
                records.append(record)
    return records


def walk_dicts(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_dicts(value)


def walk_strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)


def iter_tool_calls(record: dict[str, Any]):
    for node in walk_dicts(record):
        calls = node.get("toolCalls")
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, dict):
                    yield call


def tool_call_text(call: dict[str, Any]) -> str:
    name = call.get("name")
    args = call.get("args")
    result = call.get("result")
    parts = [str(name)]
    if isinstance(args, dict):
        for key in ("command", "query", "file_path"):
            value = args.get(key)
            if isinstance(value, str):
                parts.append(value)
    parts.extend([short(args, 2000), short(result, 2000)])
    return "\n".join(parts)


def unique_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def find_prompt_mentions(record: dict[str, Any], keywords: list[str]) -> list[str]:
    if record.get("type") != "user":
        return []
    mentions: list[str] = []
    for text in walk_strings(record):
        if any(keyword.lower() in text.lower() for keyword in keywords):
            mentions.append(short(text, 300))
    return mentions


def summarize(path: Path, *, keywords: list[str], include_thoughts: bool) -> dict[str, Any]:
    records = load_jsonl(path)
    summary: dict[str, Any] = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "records": len(records),
        "tool_tokens": [],
        "tool_calls": [],
        "frame_calls": [],
        "read_extra_frame_calls": [],
        "web_searches": [],
        "prompt_mentions": [],
        "final_outputs": [],
        "relevant_thoughts": [],
    }

    for record in records:
        line_no = record.get("_line_no")
        if record.get("type") == "gemini":
            tokens = record.get("tokens")
            if isinstance(tokens, dict):
                summary["tool_tokens"].append(tokens.get("tool"))
            content = record.get("content")
            if isinstance(content, str) and content.strip():
                summary["final_outputs"].append(
                    {
                        "line": line_no,
                        "looks_like_srt": bool(SRT_START_RE.search(content.strip())),
                        "length": len(content),
                        "snippet": short(content, 350),
                    }
                )
            if include_thoughts:
                for thought in record.get("thoughts") or []:
                    if not isinstance(thought, dict):
                        continue
                    text = f"{thought.get('subject', '')} {thought.get('description', '')}"
                    if any(keyword.lower() in text.lower() for keyword in keywords):
                        summary["relevant_thoughts"].append(
                            {
                                "line": line_no,
                                "subject": thought.get("subject"),
                                "description": short(thought.get("description", ""), 600),
                            }
                        )

        for mention in find_prompt_mentions(record, keywords):
            summary["prompt_mentions"].append({"line": line_no, "snippet": mention})

        for call in iter_tool_calls(record):
            name = call.get("name")
            args = call.get("args")
            result = call.get("result")
            call_text = tool_call_text(call)
            tool_summary = {
                "line": line_no,
                "name": name,
                "status": call.get("status"),
                "args": short(args, 700),
                "result": short(result, 700),
            }
            summary["tool_calls"].append(tool_summary)

            frame_match = FRAME_COMMAND_RE.search(call_text)
            if frame_match:
                times = [
                    item.strip()
                    for item in frame_match.group("times").split(",")
                    if item.strip()
                ]
                summary["frame_calls"].append(
                    {
                        **tool_summary,
                        "stage": frame_match.group("stage"),
                        "project_dir": frame_match.group("project"),
                        "times": times,
                        "frame_paths": unique_in_order([
                            Path(match).name for match in FRAME_PATH_RE.findall(call_text)
                        ]),
                    }
                )

            if name == "read_file" and "extra_frames" in call_text:
                summary["read_extra_frame_calls"].append(tool_summary)

            if name == "google_web_search":
                query = args.get("query") if isinstance(args, dict) else None
                summary["web_searches"].append({**tool_summary, "query": query})

    return summary


def print_text(summary: dict[str, Any]) -> None:
    print(f"\n=== {summary['path']} ===")
    print(f"records={summary['records']} bytes={summary['bytes']}")
    print(f"tool_tokens={summary['tool_tokens']}")
    print(
        "tool_calls={tool} frame_calls={frames} read_extra_frames={reads} "
        "web_searches={searches} prompt_mentions={mentions} final_outputs={finals}".format(
            tool=len(summary["tool_calls"]),
            frames=len(summary["frame_calls"]),
            reads=len(summary["read_extra_frame_calls"]),
            searches=len(summary["web_searches"]),
            mentions=len(summary["prompt_mentions"]),
            finals=len(summary["final_outputs"]),
        )
    )
    for frame in summary["frame_calls"]:
        print(
            f"FRAME line={frame['line']} stage={frame['stage']} "
            f"n_times={len(frame['times'])} times={','.join(frame['times'])}"
        )
        if frame["frame_paths"]:
            print(f"  files={', '.join(frame['frame_paths'])}")
    for search in summary["web_searches"]:
        print(f"SEARCH line={search['line']} query={search.get('query')!r}")
    for output in summary["final_outputs"]:
        print(
            f"OUTPUT line={output['line']} srt={output['looks_like_srt']} "
            f"len={output['length']} snippet={output['snippet']}"
        )
    for thought in summary["relevant_thoughts"][:12]:
        print(
            f"THOUGHT line={thought['line']} {thought.get('subject')}: "
            f"{thought.get('description')}"
        )


def main(argv: list[str] | None = None) -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--keywords",
        default="get_frames,extra_frames,run_shell_command,read_file,google_web_search,frame,image,visual,caption,search,web",
        help="Comma-separated keywords to highlight in prompt/thought text.",
    )
    parser.add_argument("--show-thoughts", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    summaries: list[dict[str, Any]] = []
    for raw_path in args.paths:
        files = session_files(raw_path)
        if not files:
            print(f"No session JSONL files found under {raw_path}", file=sys.stderr)
            continue
        for file in files:
            summaries.append(
                summarize(file, keywords=keywords, include_thoughts=args.show_thoughts)
            )

    if args.as_json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        for item in summaries:
            print_text(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
