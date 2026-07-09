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
from pydantic import BaseModel, ValidationError

from lib import script_review
from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.episode_ledger import episode_outline_context
from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    STEP1_FILENAMES,
    STEP1_LEGACY_FILENAMES,
    episode_drafts_dir,
)
from lib.json_io import atomic_write_json
from lib.project_manager import DEFAULT_SOURCE_KIND, effective_mode
from lib.prompt_builders_script import build_normalize_prompt
from lib.script_generator import ScriptGenerator
from lib.script_models import build_drama_normalized_script_model
from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.agent_runtime.sdk_tools._context import ToolContext, fetch_video_caps, tool_error

logger = logging.getLogger(__name__)

_FALLBACK_SUPPORTED_DURATIONS: list[int] = [4, 6, 8]


def _parse_normalized_content(response_text: str, model: type[BaseModel]) -> dict:
    """解析并校验 step1 结构化内容响应为 dict；校验失败 fail-loud 抛 ValueError，不返回未校验内容。

    ``model`` 取自调用处用 ``supported_durations`` 构造的同一份动态 schema（即 response_schema），
    令本地校验与 response_schema 同口径：即使 backend 未严格执行 schema，超出 supported_durations
    的 duration_seconds、缺字段、坏 utterances 也在此被拦截。校验失败抛错而非降级保留原始 JSON——
    否则未校验内容会被当成正式 step1_normalized_script.json 落盘（下游 _load_drama_step1_content 仅守
    最外层形状、放行），把非法时长 / 缺字段拖到 step2 或最终 save_script 才暴露。与 narration 的
    _load_narration_step1 严格读取同口径：只有经 schema 校验的内容才成为持久化的 step1 真值源。
    """
    text = strip_json_code_fences(response_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"step1 规范化内容 JSON 解析失败: {e}")
    if not isinstance(data, dict):
        raise ValueError("step1 规范化内容结构异常：顶层应为对象 {title, scenes}")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as e:
        raise ValueError(f"step1 规范化内容结构校验失败: {e}") from e


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


def _resolve_step1_path(project_path: Path, episode: int, project_data: dict[str, Any]) -> tuple[Path, str] | None:
    """Return (step1_md path, hint text for missing-file error)；ad 一键生成不依赖 step1，返回 None。"""
    content_mode = project_data.get("content_mode", "narration")
    if content_mode == "ad":
        # ad 创作输入是 project.json 的 brief + 产品信息 + target_duration，
        # ScriptGenerator 的 ad 分支不读 drafts/ 中间文件。
        return None
    episode_dict = next(
        (ep for ep in (project_data.get("episodes") or []) if ep.get("episode") == episode),
        {},
    )
    generation_mode = effective_mode(project=project_data, episode=episode_dict)
    drafts_path = episode_drafts_dir(project_path, episode)
    if generation_mode == "reference_video":
        return drafts_path / REFERENCE_VIDEO_STEP1_FILENAME, "split-reference-video-units subagent (Step 1)"
    if content_mode != "narration" and content_mode in STEP1_FILENAMES:
        # drama 及未来其它走 drama 形状两段式的结构化模式：step1 是结构化 JSON（见 ADR 0041）。
        # narration 虽也在 STEP1_FILENAMES，但另有旧 .md 迁移提示分支，需先排除。
        return drafts_path / STEP1_FILENAMES[content_mode], "normalize_drama_script tool"
    # narration 生成需结构化 step1 JSON；仅存旧版 .md 时给出与
    # ScriptGenerator._load_narration_step1 一致的重切迁移提示，而非笼统的缺文件错误。
    narration_json = STEP1_FILENAMES["narration"]
    narration_legacy_md = STEP1_LEGACY_FILENAMES["narration"][0]
    step1_json = drafts_path / narration_json
    if not step1_json.exists() and (drafts_path / narration_legacy_md).exists():
        return step1_json, f"重跑 split-narration-segments 把旧 {narration_legacy_md} 重新拆分为结构化 {narration_json}"
    return step1_json, "split-narration-segments subagent (Step 1)"


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

            step1 = _resolve_step1_path(project_path, episode, project_data)
            if step1 is not None:
                step1_path, hint = step1
                if not step1_path.exists():
                    return {
                        "content": [
                            {"type": "text", "text": f"❌ 未找到 Step 1 文件: {step1_path}\n   请先完成 {hint}"}
                        ],
                        "is_error": True,
                    }

            if dry_run:
                generator = ScriptGenerator(project_path)
                prompt = await generator.build_prompt(episode)
                return {
                    "content": [{"type": "text", "text": f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}"}]
                }

            # step1→step2 审核 gate：drama / narration 的结构化 step1 中间态须经 web 显式确认才放行
            # step2 视觉生成；未确认（或确认后内容又被改）时阻塞，引导用户先在 Web 端审阅确认。
            # ad（无 step1）/ reference_video（step1 为自由文本 md）不适用，gate 自动放行。
            if script_review.gate_blocks_step2(project_path, project_data, episode):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "⏸️ step1 结构化中间态尚未经 web 审核确认，step2 视觉生成被 gate 阻塞。"
                                "请在 Web 端审阅并确认本集 step1 内容后再生成剧本。"
                            ),
                        }
                    ],
                    "is_error": True,
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
# confirm_script_review
# ---------------------------------------------------------------------------


def confirm_script_review_tool(ctx: ToolContext):
    @tool(
        "confirm_script_review",
        "确认本集 step1 结构化中间态（drama / narration 的逐字口播 / 原文），放行 step2 视觉生成。"
        "仅在用户对话中明确同意进入视觉生成、或已在 Web 端审阅认可后调用——这是 step2 的显式确认动作，"
        "与 Web 端确认等价；未确认时 generate_episode_script 会被审核 gate 阻塞。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "description": "剧集编号"}},
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            # 延迟导入避免 sdk_tools 在导入期耦合 server.services。
            from server.services.script_review import ScriptReviewError, ScriptReviewService

            service = ScriptReviewService(ctx.pm)
            try:
                state = service.confirm(ctx.project_name, episode)
            except ScriptReviewError as exc:
                return {
                    "content": [
                        {"type": "text", "text": f"❌ 无法确认 step1 审核（{exc.code}）：{exc.message or exc.code}"}
                    ],
                    "is_error": True,
                }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"✅ 第 {episode} 集 step1 已确认，step2 视觉生成已放行（status={state['status']}）",
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("confirm_script_review", exc)

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
        "把 source/ 小说原文（或指定 source 文件）抽取为结构化分镜内容（场景边界、出场资产、"
        "逐字口播 utterances、原文锚 source_text、视觉改编描述），保存到 "
        "drafts/episode_N/step1_normalized_script.json，供 generate_episode_script 透传消费。"
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
            # 分集大纲（故事节点 / 钩子）随内容抽取前移到 step1，驱动内容覆盖与末场落地（见 ADR 0041）。
            episode_outline, next_episode_outline = episode_outline_context(project, episode)
            prompt = build_normalize_prompt(
                novel_text=novel_text,
                project_overview=project.get("overview", {}),
                style=project.get("style", ""),
                characters=project.get("characters", {}),
                scenes=project.get("scenes", {}),
                props=project.get("props", {}),
                default_duration=default_duration,
                supported_durations=supported_durations,
                episode=episode,
                source_kind=project.get("source_kind") or DEFAULT_SOURCE_KIND,
                episode_outline=episode_outline,
                next_episode_outline=next_episode_outline,
                # 输出语言取项目 source_language（生成内容语言的唯一真相源）；缺省回退默认中文，
                # 非中文项目的 step1 内容据此用目标语言产出，而非默认中文。
                target_language=project.get("source_language") or "中文",
                # source_language（zh / en / vi 或 None）另供时长下界软指引取语速：drama step1 引导模型为
                # 每场选不低于该场 utterances 口播时长的档位，语速按此从 lib.speech_rate 单一真相源注入。
                source_language=project.get("source_language"),
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

            # 结构化输出：response_schema 约束为 duration 枚举硬约束的规范化剧本模型（按
            # supported_durations 动态构造），直接产出 utterances + source_text + 视觉描述，
            # 消除「结构→自由文本→结构」双重转写。本地解析复用同一 schema 保持同口径校验。
            schema = build_drama_normalized_script_model(supported_durations)
            generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=ctx.project_name)
            result = await generator.generate(
                TextGenerationRequest(
                    prompt=prompt,
                    response_schema=schema,
                    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                ),
                project_name=ctx.project_name,
            )
            content = _parse_normalized_content(result.text, schema)

            # 与 _load_drama_step1_content 的读取契约同口径：scenes 须为非空列表，避免把空 / 形状坏的
            # step1 当成功产物写盘、拖到 step2 / 最终生成才必然失败。
            raw_scenes = content.get("scenes")
            if not isinstance(raw_scenes, list) or not raw_scenes:
                raise ValueError("step1 规范化内容结构异常：scenes 必须是非空的场景对象数组")

            drafts_dir = episode_drafts_dir(project_path, episode)
            drafts_dir.mkdir(parents=True, exist_ok=True)
            step1_path = drafts_dir / STEP1_FILENAMES["drama"]
            # step1 真相源须原子写入：复用 atomic_write_json（同目录 tempfile + os.replace），
            # 避免 normalize 中断 / 并发重跑留下半写 JSON 被下游当成损坏草稿。
            atomic_write_json(step1_path, content)

            scenes = raw_scenes
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"✅ 规范化剧本（结构化内容）已保存: {step1_path}\n📊 生成统计: {len(scenes)} 个场景",
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("normalize_drama_script", exc)

    return _handler


__all__ = [
    "get_video_capabilities_tool",
    "generate_episode_script_tool",
    "confirm_script_review_tool",
    "normalize_drama_script_tool",
]
