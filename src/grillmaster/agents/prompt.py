"""Prompt sections the runner adds around a stage's own text.

Stages write what to do; this module adds how to reach the grill tools,
how to open media on backends that need `view_file`, and the repair-round
message. Detailed limits stay in each MCP tool's own description.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.prompts import join_sections

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.core.tool_session import ToolSession


def compose_message(
    instructions: str,
    prompt: str,
    *,
    tools: ToolSession | None,
    view_file_images: Sequence[Path] = (),
    view_file_audio: Sequence[Path] = (),
) -> str:
    """The single user message every backend receives."""
    return join_sections(
        instructions,
        prompt,
        tools_section(tools) if tools is not None else None,
        view_file_section(view_file_images, view_file_audio),
    )


def tools_section(tools: ToolSession) -> str:
    lines: list[str] = []
    if tools.frames is not None:
        start, end = tools.frames.window
        until = f"{end:.3f} 秒" if end is not None else "影片結尾"
        lines.append(
            "- `get_frames(times)`：擷取影片指定秒數的畫面，直接以圖片回傳"
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
        "開始作業前，先用 view_file 工具逐一開啟下列檔案，讓檔案內容本身進入你的脈絡：",
    ]
    if images:
        lines += ["圖片：", *(f"- {path}" for path in images)]
    if audio:
        lines += [
            "音訊（直接聆聽；不要用 shell 指令、腳本、ffmpeg 或語音辨識工具轉寫或分析）：",
            *(f"- {path}" for path in audio),
        ]
    return "\n".join(lines)


def repair_message(failure: str) -> str:
    """The whole user message of a repair round: only what was wrong."""
    return (
        f"【修正要求】你上一輪的輸出未通過驗證：\n{failure}\n\n"
        "請修正上述問題，以原本要求的方式與格式重新輸出完整結果。"
    )
