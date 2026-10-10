"""Prompt sections the runner adds around a stage's own text.

Stages write what to do; this module adds how to reach the grill tools,
how to open media on backends that need `view_file`, the message that
delivers requested frames on backends whose tool results cannot carry
images, how to submit a
schema answer on backends that take it through a `finish` tool or as the
final message, the
audio-unavailable protocol, and the repair-round message. Detailed limits
stay in each MCP tool's own description.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from grillmaster.agents.adapters.base import (
    MediaDelivery,
    SchemaDelivery,
    ToolImageDelivery,
)
from grillmaster.core.prompts import join_sections

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.agents.schema import JsonSchema
    from grillmaster.core.tool_session import ToolSession

# The line a model writes when it cannot hear its audio (`audio_check_section`).
AUDIO_UNAVAILABLE_MARKER = "GRILL_AUDIO_UNAVAILABLE"
# Decoration a model may wrap the marker line in; trimmed off the reason too.
_DECORATION = " \t`*"
_AUDIO_UNAVAILABLE_RE = re.compile(
    rf"^[{_DECORATION}]*{AUDIO_UNAVAILABLE_MARKER}[{_DECORATION}]*[:：]?(.*)$",
    re.MULTILINE,
)


def compose_message(
    instructions: str,
    prompt: str,
    *,
    tools: ToolSession | None,
    images: Sequence[Path] = (),
    audio: Sequence[Path] = (),
    media_delivery: MediaDelivery = MediaDelivery.ATTACHED,
    tool_images: ToolImageDelivery = ToolImageDelivery.INLINE,
    schema: JsonSchema | None = None,
    schema_delivery: SchemaDelivery = SchemaDelivery.NATIVE,
) -> str:
    """The single user message every backend receives, shaped by how the
    backend takes input media, frames-tool images and a schema answer.

    `VIEW_FILE` media: the model opens them itself, so their paths are
    listed. A `schema` on a `FINISH_TOOL` backend is submitted through the
    CLI's `finish` tool; on a `PROMPT` backend the message states it. Audio
    inputs bring the audio-unavailable protocol.
    """
    submit = None
    if schema is not None:
        match schema_delivery:
            case SchemaDelivery.FINISH_TOOL:
                submit = finish_section()
            case SchemaDelivery.PROMPT:
                submit = answer_schema_section(schema)
            case SchemaDelivery.NATIVE:
                pass
    view_file = media_delivery is MediaDelivery.VIEW_FILE
    return join_sections(
        instructions,
        prompt,
        tools_section(tools, tool_images) if tools is not None else None,
        view_file_section(images, audio) if view_file else None,
        audio_check_section() if audio else None,
        submit,
    )


def tools_section(
    tools: ToolSession, tool_images: ToolImageDelivery = ToolImageDelivery.INLINE
) -> str:
    lines: list[str] = []
    if tools.frames is not None:
        start, end = tools.frames.window
        until = f"{end:.3f} 秒" if end is not None else "影片結尾"
        match tool_images:
            case ToolImageDelivery.INLINE:
                returned = "直接以圖片回傳"
            case ToolImageDelivery.VIEW_FILE:
                returned = (
                    "回傳畫面的圖片檔路徑；要看到畫面，須在下一輪用 view_file "
                    "同時開啟全部回傳的路徑"
                )
            case ToolImageDelivery.NEXT_MESSAGE:
                returned = (
                    "畫面不在工具結果裡，而是附在下一則訊息；"
                    "呼叫後就簡短說明並結束這一輪，收到畫面再繼續"
                )
        lines.append(
            f"- `get_frames(times)`：擷取影片指定秒數的畫面，{returned}"
            f"（時間需介於 {start:.3f} 秒與{until}之間）。"
        )
    if tools.check_srt is not None:
        lines.append(
            "- `check_srt(path)`：以參考字幕檢查候選 SRT 的骨架"
            "（區塊數、編號、時間碼、非空），回傳 VALID 或錯誤清單。"
        )
    if not lines:
        return ""
    return "\n".join(["【可用工具】", "可以呼叫 grill MCP server 的下列工具：", *lines])


def view_file_section(images: Sequence[Path], audio: Sequence[Path]) -> str:
    """Absolute paths the model must open itself with `view_file`."""
    if not images and not audio:
        return ""
    lines = [
        "【輸入檔案】",
        (
            "開始作業前，先在同一輪同時呼叫 view_file 開啟下列全部檔案"
            "（不要分批，也不要逐一等待結果），讓檔案內容本身進入你的脈絡。"
            "路徑請照抄，不要改寫任何字元："
        ),
    ]
    if images:
        lines += ["圖片：", *(f"- {path}" for path in images)]
    if audio:
        lines += [
            "音訊（直接聆聽；不要用 shell 指令、腳本、ffmpeg 或語音辨識工具轉寫或分析）：",
            *(f"- {path}" for path in audio),
        ]
    return "\n".join(lines)


def audio_check_section() -> str:
    """The way out when the model cannot hear its audio: one marker line,
    on which the runner stops the session (`audio_unavailable`)."""
    return (
        "【聽不到音訊時】\n"
        "若音訊沒有以聲音進入你的脈絡，不要猜測或改用其他工具，"
        "只回覆這一行，任務會停止並回報錯誤：\n"
        f"{AUDIO_UNAVAILABLE_MARKER}: <你實際收到了什麼>"
    )


def audio_unavailable(text: str) -> str | None:
    """The model's reason when `text` holds the audio-unavailable marker
    line (possibly empty), else `None`."""
    match = _AUDIO_UNAVAILABLE_RE.search(text)
    return match.group(1).strip(_DECORATION) if match else None


def finish_section() -> str:
    """How a schema answer is submitted on a `finish`-tool backend."""
    return (
        "【提交結果】\n"
        "完成後呼叫 finish 工具提交結果：把要求的 JSON 物件的欄位直接作為 finish 的參數。"
        "只有 finish 的參數算是提交；不要把 JSON 寫成文字訊息，"
        "也不要在呼叫 finish 之前先把結果貼出來。"
    )


def answer_schema_section(schema: JsonSchema) -> str:
    """How a schema answer is submitted on a backend without a structured
    channel: the final message is the JSON object itself."""
    return (
        "【提交結果】\n"
        "完成後，最後一則訊息只輸出一個符合下列 JSON Schema 的 JSON 物件，"
        "前後不要加任何說明文字（可以包在一個 ```json 區塊裡）：\n"
        f"```json\n{json.dumps(schema, ensure_ascii=False)}\n```"
    )


def frames_message() -> str:
    """The whole user message that delivers frames the model asked for
    (`ToolImageDelivery.NEXT_MESSAGE`); the backend attaches the images."""
    return "【畫面】你用 get_frames 要求的畫面已附在這則訊息，請據此繼續完成任務。"


def repair_message(failure: str) -> str:
    """The whole user message of a repair round: only what was wrong."""
    return (
        f"【修正要求】你上一輪的輸出未通過驗證：\n{failure}\n\n"
        "請修正上述問題，以原本要求的方式與格式重新輸出完整結果。"
    )
