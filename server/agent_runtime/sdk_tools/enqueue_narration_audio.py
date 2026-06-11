"""SDK MCP tool for narration audio (TTS) generation.

工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
    batch_enqueue_and_wait,
)
from lib.resource_paths import resource_relative_path
from lib.storyboard_sequence import get_storyboard_items
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename


def _has_voiceable_text(item: dict[str, Any]) -> bool:
    text = item.get("novel_text")
    return isinstance(text, str) and bool(text.strip())


def _select_items(items: list[dict[str, Any]], id_field: str, segment_ids: list[str] | None) -> list[dict[str, Any]]:
    # ``None`` 和 ``[]`` 含义不同：``None`` = "不传过滤，默认扫所有缺旁白音频项"；
    # ``[]`` = "显式空选择，应当返回空列表交由 handler 报错"。
    if segment_ids is not None:
        wanted = {str(s) for s in segment_ids}
        return [item for item in items if str(item.get(id_field)) in wanted]
    return [item for item in items if not (item.get("generated_assets") or {}).get("narration_audio")]


def generate_narration_audio_tool(ctx: ToolContext):
    @tool(
        "generate_narration_audio",
        "为说书（narration）模式剧本逐段生成旁白配音（TTS），入队并等待完成。"
        "script 为剧本文件名（如 episode_1.json）；segment_ids 指定片段 ID 列表"
        "（不传则扫描所有缺旁白音频的段；传列表为批量范围；单元素列表即单段重生）。"
        "音频以各段 novel_text 原文合成，只依赖剧本，不依赖分镜图/视频。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "segment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "片段 ID 列表；不传则扫描所有缺旁白音频的段",
                },
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            segment_ids = args.get("segment_ids")
            if segment_ids is not None and not isinstance(segment_ids, list):
                raise ValueError(f"segment_ids 必须是片段 ID 数组，收到: {segment_ids!r}")

            script = ctx.pm.load_script(ctx.project_name, script_filename)
            if script.get("content_mode") == "drama":
                raise ValueError("旁白配音仅适用说书（narration）模式剧本；drama 模式的 scenes 没有 novel_text")

            items, id_field, *_ = get_storyboard_items(script)
            if not items:
                # reference_video 模式 get_storyboard_items 硬返回空列表；空剧本同样
                # 无可配音项。两者都不能落进"✨ 已全部生成"的假成功分支。
                if script.get("generation_mode") == "reference_video":
                    raise ValueError("参考生视频（reference_video）模式剧本没有 segments，不适用旁白配音")
                raise ValueError("剧本没有可配音的片段")

            explicit = segment_ids is not None
            selected = _select_items(items, id_field, segment_ids)
            unmatched: list[str] = []
            if explicit:
                found = {str(item.get(id_field)) for item in selected}
                # dict.fromkeys 去重并保序：同一个未命中 id 重复传入只报一次
                unmatched = [s for s in dict.fromkeys(str(s) for s in segment_ids or []) if s not in found]
            if not selected:
                # 区分两种零结果：显式 segment_ids 全部不命中（[] 与不命中等价）按错误
                # 处理 vs 扫描模式下全部已生成（真无事可做）。
                if explicit:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"❌ 没有找到匹配的片段：segment_ids={segment_ids}",
                            }
                        ],
                        "is_error": True,
                    }
                return {"content": [{"type": "text", "text": "✨ 所有片段的旁白音频都已生成"}]}

            # 缺 id 的片段（损坏/手改剧本）不能让整批 KeyError 中断，跳过并告警。
            identified = [item for item in selected if item.get(id_field)]
            missing_id_count = len(selected) - len(identified)
            voiceable = [item for item in identified if _has_voiceable_text(item)]
            blank = [str(item[id_field]) for item in identified if not _has_voiceable_text(item)]
            specs = [
                TaskSpec.from_request(
                    task_type="tts",
                    media_type="audio",
                    resource_id=str(item[id_field]),
                    prompt=item["novel_text"],
                    script_file=script_filename,
                )
                for item in voiceable
            ]

            successes: list[BatchTaskResult] = []
            failures: list[BatchTaskResult] = []
            if specs:
                successes, failures = await batch_enqueue_and_wait(
                    project_name=ctx.project_name,
                    specs=specs,
                )

            details: list[str] = []
            for br in successes:
                result = br.result or {}
                rel = result.get("file_path") or resource_relative_path("audio", br.resource_id)
                details.append(f"  ✓ {br.resource_id} → {rel}")
            for f in failures:
                details.append(f"  ✗ {f.resource_id}: {f.error}")
            # 空白段不能静默丢弃：扫描模式下是告警（该段永远无法配音，不阻塞其余段）；
            # 显式点名时按失败处理——调用方明确要这一段，给"成功 0 失败 0"会误导。
            mark = "✗" if explicit else "⚠️"
            for sid in blank:
                details.append(f"  {mark} {sid}: novel_text 为空，无法配音")
            for sid in unmatched:
                details.append(f"  ✗ {sid}: 片段不存在")
            if missing_id_count:
                details.append(f"  ⚠️ 跳过 {missing_id_count} 个缺少 {id_field} 的片段")

            failed_count = len(failures) + len(unmatched) + (len(blank) if explicit else 0)
            header = f"generate_narration_audio summary: {len(successes)} succeeded, {failed_count} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([header, *details])}],
                "is_error": failed_count > 0,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_narration_audio", exc)

    return _handler


__all__ = ["generate_narration_audio_tool"]
