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
from lib.prompt_builders_script import build_normalize_prompt
from lib.script_generator import ScriptGenerator
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_backends.factory import create_text_backend_for_task
from server.agent_runtime.sdk_tools._context import ToolContext, fetch_video_caps, tool_error

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
            return tool_error("get_video_capabilities", exc)

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
            return tool_error("generate_episode_script", exc)

    return _handler


# ---------------------------------------------------------------------------
# normalize_drama_script
# ---------------------------------------------------------------------------


async def _fetch_caps_with_fallback(project: dict[str, Any]) -> tuple[int | None, list[int]]:
    """Script normalization is best-effort: prompt生成 不该被能力查询失败堵住。

    Soft-fallbacks to ``_FALLBACK_SUPPORTED_DURATIONS`` so the LLM still
    receives a usable duration constraint set if the resolver hiccups.
    """
    try:
        default_int, durations = await fetch_video_caps(project)
    except (FileNotFoundError, ValueError) as exc:
        logger.info("video_capabilities 不可解析，使用 fallback [4,6,8]：%s", exc)
        return None, list(_FALLBACK_SUPPORTED_DURATIONS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_capabilities 查询异常，使用 fallback [4,6,8]：%s", exc)
        return None, list(_FALLBACK_SUPPORTED_DURATIONS)
    if not durations:
        return default_int, list(_FALLBACK_SUPPORTED_DURATIONS)
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

            default_duration, supported_durations = await _fetch_caps_with_fallback(project)
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

            backend = await create_text_backend_for_task(TextTaskType.SCRIPT, ctx.project_name)
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
            return tool_error("normalize_drama_script", exc)

    return _handler


__all__ = [
    "get_video_capabilities_tool",
    "generate_episode_script_tool",
    "normalize_drama_script_tool",
]
