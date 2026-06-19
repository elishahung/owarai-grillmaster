#!/usr/bin/env python
"""Audit Codex rollout/session JSONL files without confusing prompts for tools."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SRT_START_RE = re.compile(r"^\d+\n\d\d:\d\d:\d\d,\d{3}\s+-->\s+", re.MULTILINE)
JSON_OBJECT_RE = re.compile(r"^\s*\{.*\}\s*$", re.DOTALL)


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


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
    files = sorted(path.glob("rollout-*.jsonl"))
    if files:
        return files
    return sorted(path.glob("*.jsonl"))


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text") or item.get("input_text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts)


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


def walk_strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)


def collect_images(node: Any) -> list[str]:
    images: list[str] = []
    if isinstance(node, dict):
        for key in ("local_images", "images"):
            value = node.get(key)
            if isinstance(value, list):
                images.extend(str(item) for item in value)
        for value in node.values():
            images.extend(collect_images(value))
    elif isinstance(node, list):
        for value in node:
            images.extend(collect_images(value))
    return images


def maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def output_status(output: str) -> str | None:
    first = output.splitlines()[0].strip() if output.splitlines() else ""
    return first or None


def keyword_hit(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def summarize(
    path: Path,
    *,
    keywords: list[str],
    include_prompts: bool,
    include_messages: bool,
) -> dict[str, Any]:
    records = load_jsonl(path)
    summary: dict[str, Any] = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "records": len(records),
        "session": {},
        "turn": {},
        "prompt_mentions": [],
        "messages": [],
        "reasoning": [],
        "tool_calls": [],
        "tool_outputs": {},
        "local_images": {"count": 0, "examples": []},
        "final_outputs": [],
        "event_messages": [],
    }

    image_examples: list[str] = []

    for record in records:
        line_no = record.get("_line_no")
        record_type = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        image_examples.extend(collect_images(payload))

        if record_type == "session_meta":
            summary["session"] = {
                "id": payload.get("id"),
                "timestamp": payload.get("timestamp"),
                "cwd": payload.get("cwd"),
                "originator": payload.get("originator"),
                "source": payload.get("source"),
                "thread_source": payload.get("thread_source"),
                "model_provider": payload.get("model_provider"),
                "cli_version": payload.get("cli_version"),
            }
            if include_prompts:
                base = payload.get("base_instructions")
                for text in walk_strings(base):
                    if keyword_hit(text, keywords):
                        summary["prompt_mentions"].append(
                            {"line": line_no, "source": "session_meta", "snippet": short(text, 350)}
                        )

        elif record_type == "turn_context":
            summary["turn"] = {
                "turn_id": payload.get("turn_id"),
                "cwd": payload.get("cwd"),
                "model": payload.get("model"),
                "effort": payload.get("effort"),
                "approval_policy": payload.get("approval_policy"),
                "sandbox_policy": payload.get("sandbox_policy"),
            }

        elif record_type == "event_msg":
            message = payload.get("message") or payload.get("msg") or payload.get("text")
            if isinstance(message, str) and message:
                summary["event_messages"].append({"line": line_no, "message": short(message, 300)})

        elif record_type == "response_item":
            payload_type = payload.get("type")
            if payload_type == "message":
                role = payload.get("role")
                text = text_from_content(payload.get("content"))
                if include_messages and text:
                    summary["messages"].append(
                        {"line": line_no, "role": role, "phase": payload.get("phase"), "snippet": short(text, 700)}
                    )
                if include_prompts and role == "user" and keyword_hit(text, keywords):
                    summary["prompt_mentions"].append(
                        {"line": line_no, "source": "user_message", "snippet": short(text, 500)}
                    )
                if role == "assistant" and text.strip():
                    stripped = text.strip()
                    summary["final_outputs"].append(
                        {
                            "line": line_no,
                            "phase": payload.get("phase"),
                            "length": len(stripped),
                            "looks_like_srt": bool(SRT_START_RE.search(stripped)),
                            "looks_like_json": bool(JSON_OBJECT_RE.match(stripped)),
                            "snippet": short(stripped, 500),
                        }
                    )

            elif payload_type == "reasoning":
                reasoning_summary = payload.get("summary")
                encrypted = bool(payload.get("encrypted_content"))
                visible: list[str] = []
                if isinstance(reasoning_summary, str) and reasoning_summary.strip():
                    visible.append(reasoning_summary)
                elif isinstance(reasoning_summary, list):
                    for item in reasoning_summary:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            visible.append(item["text"])
                        elif isinstance(item, str):
                            visible.append(item)
                summary["reasoning"].append(
                    {
                        "line": line_no,
                        "encrypted": encrypted,
                        "visible_summary_count": len(visible),
                        "visible_summary": [short(item, 500) for item in visible],
                    }
                )

            elif payload_type == "function_call":
                args = maybe_json(payload.get("arguments"))
                call = {
                    "line": line_no,
                    "call_id": payload.get("call_id"),
                    "name": payload.get("name"),
                    "arguments": args,
                    "argument_snippet": short(args, 900),
                }
                summary["tool_calls"].append(call)

            elif payload_type == "function_call_output":
                call_id = payload.get("call_id")
                output = payload.get("output") or ""
                summary["tool_outputs"][call_id] = {
                    "line": line_no,
                    "status": output_status(output),
                    "length": len(output),
                    "snippet": short(output, 900),
                }

    summary["local_images"] = {
        "count": len(image_examples),
        "examples": image_examples[:12],
    }

    for call in summary["tool_calls"]:
        call["output"] = summary["tool_outputs"].get(call.get("call_id"))

    return summary


def print_text(summary: dict[str, Any]) -> None:
    session = summary["session"]
    turn = summary["turn"]
    print(f"\n=== {summary['path']} ===")
    print(f"records={summary['records']} bytes={summary['bytes']}")
    print(
        "id={id} cwd={cwd} originator={originator} provider={provider} model={model}".format(
            id=session.get("id"),
            cwd=session.get("cwd"),
            originator=session.get("originator"),
            provider=session.get("model_provider"),
            model=turn.get("model"),
        )
    )
    print(
        "tool_calls={tools} local_images={images} reasoning_items={reasoning} "
        "prompt_mentions={mentions} assistant_outputs={outputs}".format(
            tools=len(summary["tool_calls"]),
            images=summary["local_images"]["count"],
            reasoning=len(summary["reasoning"]),
            mentions=len(summary["prompt_mentions"]),
            outputs=len(summary["final_outputs"]),
        )
    )
    for call in summary["tool_calls"]:
        print(f"TOOL line={call['line']} name={call['name']} call_id={call['call_id']}")
        print(f"  args={call['argument_snippet']}")
        output = call.get("output")
        if output:
            print(f"  output={output.get('status')} {output.get('snippet')}")
    for item in summary["reasoning"]:
        encrypted = "encrypted" if item["encrypted"] else "not-encrypted"
        print(
            f"REASONING line={item['line']} {encrypted} "
            f"visible_summaries={item['visible_summary_count']}"
        )
        for visible in item["visible_summary"]:
            print(f"  summary={visible}")
    for output in summary["final_outputs"]:
        print(
            f"OUTPUT line={output['line']} phase={output.get('phase')} "
            f"srt={output['looks_like_srt']} json={output['looks_like_json']} "
            f"len={output['length']} snippet={output['snippet']}"
        )
    for mention in summary["prompt_mentions"][:10]:
        print(
            f"PROMPT_HIT line={mention['line']} source={mention['source']} "
            f"snippet={mention['snippet']}"
        )
    for path in summary["local_images"]["examples"][:5]:
        print(f"IMAGE {path}")


def main(argv: list[str] | None = None) -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--keywords",
        default="shell_command,function_call,get_frames,web.run,local_images,pre_pass,chunk,refine,video.cht.srt,video.ja.srt",
        help="Comma-separated keywords to highlight in prompts and metadata.",
    )
    parser.add_argument("--show-prompts", action="store_true")
    parser.add_argument("--show-messages", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    summaries: list[dict[str, Any]] = []
    for raw_path in args.paths:
        files = session_files(raw_path)
        if not files:
            print(f"No Codex JSONL files found under {raw_path}", file=sys.stderr)
            continue
        for file in files:
            summaries.append(
                summarize(
                    file,
                    keywords=keywords,
                    include_prompts=args.show_prompts,
                    include_messages=args.show_messages,
                )
            )

    if args.as_json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        for item in summaries:
            print_text(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
