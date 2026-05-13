"""SDK MCP tools for text generation (script + normalization) and capability queries.

`get_video_capabilities` ships in this module because it shares the same
`ConfigResolver.video_capabilities` plumbing as ``normalize_drama_script``;
keeping them together avoids a one-tool stub file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.project_manager import effective_mode
from lib.script_generator import ScriptGenerator
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_backends.factory import create_text_backend_for_task
from server.agent_runtime.sdk_tools._context import ToolContext

logger = logging.getLogger(__name__)

_FALLBACK_SUPPORTED_DURATIONS: list[int] = [4, 6, 8]


# ---------------------------------------------------------------------------
# get_video_capabilities
# ---------------------------------------------------------------------------


async def _resolve_video_capabilities(project_name: str) -> dict[str, Any]:
    resolver = ConfigResolver(async_session_factory)
    return await resolver.video_capabilities(project_name)


def get_video_capabilities_tool(ctx: ToolContext):
    @tool(
        "get_video_capabilities",
        "查当前项目的视频模型能力（model 粒度）+ 用户项目偏好。返回 JSON。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = await _resolve_video_capabilities(ctx.project_name)
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}
        except FileNotFoundError as exc:
            return {
                "content": [{"type": "text", "text": f"项目未找到或缺 project.json: {exc}"}],
                "is_error": True,
            }
        except ValueError as exc:
            return {
                "content": [{"type": "text", "text": f"无法解析视频模型能力: {exc}"}],
                "is_error": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"get_video_capabilities 失败: {exc}"}],
                "is_error": True,
            }

    return _handler


# ---------------------------------------------------------------------------
# generate_episode_script
# ---------------------------------------------------------------------------


def _resolve_step1_path(project_path: Path, episode: int, project_data: dict[str, Any]) -> tuple[Path, str]:
    """Return (step1_md path, hint text for missing-file error)."""
    content_mode = project_data.get("content_mode", "narration")
    episode_dict = next(
        (ep for ep in (project_data.get("episodes") or []) if ep.get("episode") == episode),
        {},
    )
    generation_mode = effective_mode(project=project_data, episode=episode_dict)
    drafts_path = project_path / "drafts" / f"episode_{episode}"
    if generation_mode == "reference_video":
        return drafts_path / "step1_reference_units.md", "split-reference-video-units subagent (Step 1)"
    if content_mode == "drama":
        return drafts_path / "step1_normalized_script.md", "normalize_drama_script tool"
    return drafts_path / "step1_segments.md", "片段拆分 (Step 1)"


def generate_episode_script_tool(ctx: ToolContext):
    @tool(
        "generate_episode_script",
        "调用项目配置的文本模型生成 JSON 剧本（agent 内置 in-process MCP tool，"
        "无 sandbox provider 域名约束）。输出固定写入 {project}/scripts/episode_N.json，"
        "dry_run=true 时仅返回 prompt 不调用 API。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            dry_run = bool(args.get("dry_run"))

            project_path = ctx.project_path
            try:
                project_data = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                project_data = {}

            step1_path, hint = _resolve_step1_path(project_path, episode, project_data)
            if not step1_path.exists():
                return {
                    "content": [{"type": "text", "text": f"❌ 未找到 Step 1 文件: {step1_path}\n   请先完成 {hint}"}],
                    "is_error": True,
                }

            if dry_run:
                generator = ScriptGenerator(project_path)
                prompt = await generator.build_prompt(episode)
                return {
                    "content": [{"type": "text", "text": f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}"}]
                }

            generator = await ScriptGenerator.create(project_path)
            result_path = await generator.generate(episode=episode)
            return {"content": [{"type": "text", "text": f"✅ 剧本生成完成: {result_path}"}]}
        except FileNotFoundError as exc:
            return {"content": [{"type": "text", "text": f"❌ 文件错误: {exc}"}], "is_error": True}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"generate_episode_script 失败: {exc}"}],
                "is_error": True,
            }

    return _handler


# ---------------------------------------------------------------------------
# normalize_drama_script
# ---------------------------------------------------------------------------


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict[str, Any],
    style: str,
    characters: dict[str, Any],
    scenes: dict[str, Any],
    props: dict[str, Any],
    default_duration: int | None,
    supported_durations: list[int],
) -> str:
    char_list = "\n".join(f"- {name}" for name in characters.keys()) or "（暂无）"
    scene_list = "\n".join(f"- {name}" for name in scenes.keys()) or "（暂无）"
    prop_list = "\n".join(f"- {name}" for name in props.keys()) or "（暂无）"

    durations_str = ", ".join(str(d) for d in supported_durations) if supported_durations else "—"
    max_dur = max(supported_durations) if supported_durations else None

    if default_duration is not None and max_dur is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 每场景默认 {default_duration} 秒；打斗、大场面、情绪铺陈等画面可取更长值至上限 {max_dur} 秒，"
            "不要默认挑最短值"
        )
    elif max_dur is not None:
        duration_rules = (
            f"- 时长：只能取 {durations_str} 中的值（该视频模型支持的秒数集合）\n"
            f"- 按画面内容复杂度匹配合适时长（最长 {max_dur} 秒），不强制默认值"
        )
    else:
        duration_rules = f"- 时长：只能取 {durations_str} 中的值"

    return f"""你的任务是将小说原文改编为结构化的分镜场景表（Markdown 格式），用于后续 AI 视频生成。

## 项目信息

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
{style}
</style>

<characters>
{char_list}
</characters>

<scenes>
{scene_list}
</scenes>

<props>
{prop_list}
</props>

## 小说原文

<novel>
{novel_text}
</novel>

## 输出要求

将小说改编为场景列表，使用 Markdown 表格格式：

| 场景 ID | 场景描述 | 时长 | 场景类型 | segment_break |
|---------|---------|------|---------|---------------|
| E{{N}}S01 | 详细的场景描述... | <duration> | 剧情 | 是 |
| E{{N}}S02 | 详细的场景描述... | <duration> | 对话 | 否 |

规则：
- 场景 ID 格式：E{{集数}}S{{两位序号}}（如 E1S01, E1S02）
- 场景描述：改编后的剧本化描述，包含角色动作、对话、环境，适合视觉化呈现
{duration_rules}
- 场景类型：剧情、动作、对话、过渡、空镜
- segment_break：场景切换点标记"是"，同一连续场景标"否"
- 每个场景应为一个独立的视觉画面，可以在指定时长内完成
- 避免一个场景包含多个不同的动作或画面切换

仅输出 Markdown 表格，不要包含其他解释文字。
"""


async def _fetch_video_caps(project_name: str) -> tuple[int | None, list[int]]:
    resolver = ConfigResolver(async_session_factory)
    try:
        caps = await resolver.video_capabilities(project_name)
    except (FileNotFoundError, ValueError) as exc:
        logger.info("video_capabilities 不可解析，使用 fallback [4,6,8]：%s", exc)
        return None, list(_FALLBACK_SUPPORTED_DURATIONS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_capabilities 查询异常，使用 fallback [4,6,8]：%s", exc)
        return None, list(_FALLBACK_SUPPORTED_DURATIONS)

    durations = list(caps.get("supported_durations") or []) or list(_FALLBACK_SUPPORTED_DURATIONS)
    default = caps.get("default_duration")
    default_int = int(default) if isinstance(default, int) else None
    return default_int, durations


def normalize_drama_script_tool(ctx: ToolContext):
    @tool(
        "normalize_drama_script",
        "把 source/ 小说原文（或指定 source 文件）转化为 Markdown 规范化剧本，保存到 "
        "drafts/episode_N/step1_normalized_script.md，供 generate_episode_script 消费。"
        "dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {
                    "type": "string",
                    "description": "指定小说源文件路径（相对项目目录）；默认读取 source/ 下所有文本",
                },
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            source = args.get("source")
            dry_run = bool(args.get("dry_run"))

            project_path = ctx.project_path
            project = ctx.pm.load_project(ctx.project_name)

            if source:
                source_path = (project_path / source).resolve()
                if not source_path.is_relative_to(project_path.resolve()):
                    return {
                        "content": [{"type": "text", "text": f"❌ 路径超出项目目录: {source_path}"}],
                        "is_error": True,
                    }
                if not source_path.exists():
                    return {
                        "content": [{"type": "text", "text": f"❌ 未找到源文件: {source_path}"}],
                        "is_error": True,
                    }
                novel_text = source_path.read_text(encoding="utf-8")
            else:
                source_dir = project_path / "source"
                if not source_dir.exists() or not any(source_dir.iterdir()):
                    return {
                        "content": [{"type": "text", "text": f"❌ source/ 目录为空或不存在: {source_dir}"}],
                        "is_error": True,
                    }
                texts = [
                    f.read_text(encoding="utf-8")
                    for f in sorted(source_dir.iterdir())
                    if f.suffix in (".txt", ".md", ".text")
                ]
                novel_text = "\n\n".join(texts)

            if not novel_text.strip():
                return {"content": [{"type": "text", "text": "❌ 小说原文为空"}], "is_error": True}

            default_duration, supported_durations = await _fetch_video_caps(ctx.project_name)
            prompt = build_normalize_prompt(
                novel_text=novel_text,
                project_overview=project.get("overview", {}),
                style=project.get("style", ""),
                characters=project.get("characters", {}),
                scenes=project.get("scenes", {}),
                props=project.get("props", {}),
                default_duration=default_duration,
                supported_durations=supported_durations,
            )

            if dry_run:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符",
                        }
                    ]
                }

            backend = await create_text_backend_for_task(TextTaskType.SCRIPT)
            result = await backend.generate(TextGenerationRequest(prompt=prompt, max_output_tokens=16000))
            response = result.text

            drafts_dir = project_path / "drafts" / f"episode_{episode}"
            drafts_dir.mkdir(parents=True, exist_ok=True)
            step1_path = drafts_dir / "step1_normalized_script.md"
            step1_path.write_text(response.strip(), encoding="utf-8")

            scene_count = sum(
                1
                for line in response.split("\n")
                if line.strip().startswith("|") and "场景 ID" not in line and "---" not in line
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"✅ 规范化剧本已保存: {step1_path}\n📊 生成统计: {scene_count} 个场景",
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"normalize_drama_script 失败: {exc}"}],
                "is_error": True,
            }

    return _handler


__all__ = [
    "get_video_capabilities_tool",
    "generate_episode_script_tool",
    "normalize_drama_script_tool",
    "build_normalize_prompt",
]
