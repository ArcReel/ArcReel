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
from lib.narration_delivery import canonical_narration_text
from lib.resource_paths import resource_relative_path
from lib.script_editor import resolve_items
from lib.script_models import get_generated_assets, resolve_creation_type
from lib.script_skeleton import ensure_route_skeleton
from lib.speech_composition import SpeechMode, admit_script_unit
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename


def _select_items(items: list[dict[str, Any]], id_field: str, segment_ids: list[str] | None) -> list[dict[str, Any]]:
    # ``None`` 和 ``[]`` 含义不同：``None`` = "不传过滤，默认扫所有缺旁白音频项"；
    # ``[]`` = "显式空选择，应当返回空列表交由 handler 报错"。
    if segment_ids is not None:
        wanted = {str(s) for s in segment_ids}
        return [item for item in items if str(item.get(id_field)) in wanted]
    return [item for item in items if not get_generated_assets(item).get("narration_audio")]


def generate_narration_audio_tool(ctx: ToolContext):
    @tool(
        "generate_narration_audio",
        "为任意路线中由 narrator 拥有发声内容的单元显式生成旁白配音（TTS），入队并等待完成。"
        "script 为剧本文件名（如 episode_1.json）；segment_ids 接受当前骨架的 unit ID 列表"
        "（不传则扫描所有缺旁白音频的 narrator 单元；单元素列表即单单元重生）。"
        "合成文本在 worker 开始时从最新剧本的规范 narrator utterances 读取，不依赖分镜图或视频。",
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
                    "description": "当前剧本骨架的单元 ID 列表；不传则扫描所有缺旁白音频的 narrator 单元",
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

            project = ctx.pm.load_project(ctx.project_name)
            creation_type = resolve_creation_type(script, project)
            ensure_route_skeleton(script, creation_type, project.get("generation_mode"))
            items, id_field, kind = resolve_items(script)
            if not items:
                raise ValueError("剧本没有可配音的单元")

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
                                "text": f"❌ 没有找到匹配的单元：segment_ids={segment_ids}",
                            }
                        ],
                        "is_error": True,
                    }
                return {"content": [{"type": "text", "text": "✨ 所有 narrator 单元的旁白音频都已生成"}]}

            # 缺 id 的片段（损坏/手改剧本）不能让整批 KeyError 中断，跳过并告警。
            identified = [item for item in selected if item.get(id_field)]
            missing_id_count = len(selected) - len(identified)
            voiceable: list[dict[str, Any]] = []
            unavailable: list[tuple[str, str]] = []
            for item in identified:
                item_id = str(item[id_field])
                admission = admit_script_unit(kind, item)
                narration_text = canonical_narration_text(admission.preparation)
                if not admission.allowed:
                    codes = ", ".join(problem.code.value for problem in admission.problems)
                    unavailable.append((item_id, codes or "speech_admission_blocked"))
                elif admission.mode is not SpeechMode.NARRATOR_VOICEOVER:
                    unavailable.append((item_id, "tts_not_applicable"))
                elif not narration_text:
                    unavailable.append((item_id, "tts_narration_text_missing"))
                else:
                    voiceable.append(item)
            specs = [
                TaskSpec.from_request(
                    task_type="tts",
                    media_type="audio",
                    resource_id=str(item[id_field]),
                    prompt=None,
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
            # 不适用或发声准入失败的单元不能静默丢弃：扫描模式下提示但不阻塞其它 narrator
            # 单元；显式点名时按失败处理。
            mark = "✗" if explicit else "⚠️"
            for unit_id, code in unavailable:
                details.append(f"  {mark} {unit_id}: {code}")
            for sid in unmatched:
                details.append(f"  ✗ {sid}: 单元不存在")
            if missing_id_count:
                details.append(f"  ⚠️ 跳过 {missing_id_count} 个缺少 {id_field} 的单元")

            failed_count = len(failures) + len(unmatched) + (len(unavailable) if explicit else 0)
            header = f"generate_narration_audio summary: {len(successes)} succeeded, {failed_count} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([header, *details])}],
                "is_error": failed_count > 0,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_narration_audio", exc)

    return _handler


__all__ = ["generate_narration_audio_tool"]
