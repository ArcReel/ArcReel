"""SDK MCP tools for text generation (script + normalization) and capability queries.

`get_video_capabilities` ships in this module because it shares the same
`ConfigResolver.video_capabilities` plumbing as ``normalize_drama_script``;
keeping them together avoids a one-tool stub file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from claude_agent_sdk import tool
from pydantic import BaseModel, ValidationError

from lib import script_review
from lib.asset_types import BUCKET_KEY
from lib.config.resolver import ConfigResolver
from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
from lib.db import async_session_factory
from lib.episode_ledger import episode_outline_context
from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_FILENAME,
    REFERENCE_VIDEO_STEP1_LEGACY_FILENAME,
    STEP1_FILENAMES,
    STEP1_LEGACY_FILENAMES,
    episode_drafts_dir,
)
from lib.i18n import _ as translate
from lib.json_io import atomic_write_json, load_json_or_none
from lib.path_safety import PathTraversalError, safe_join
from lib.project_manager import DEFAULT_SOURCE_KIND, is_reference_video_project
from lib.prompt_builders_reference import build_reference_units_split_prompt
from lib.prompt_builders_script import append_user_instructions, build_narration_split_prompt, build_normalize_prompt
from lib.reference_video.draft_validation import (
    DraftViolation,
    collect_violations,
    validate_dialogue_load,
    validate_source_text_anchor,
    validate_unit_text,
)
from lib.reference_video.quarantine import (
    PROMOTE_TOOL_NAME,
    QUARANTINE_KIND_STEP1,
    QUARANTINE_KIND_STEP2,
    STEP1_EDIT_TOOL_NAME,
    QuarantinedDraft,
    clear_quarantine,
    quarantine_and_report,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    write_quarantine,
)
from lib.reference_video.script_preview import (
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_EPISODE,
    WARN_SILENT_MODEL,
    WARN_SPEAKER_WITHOUT_AUDIO,
    derive_utterances,
    derive_voice_bindings,
)
from lib.reference_video.shot_parser import parse_prompt, render_shots_text
from lib.reference_video.voice_settings import VoiceRenderSettings
from lib.script_generator import ScriptGenerator
from lib.script_models import (
    NarrationStep1Draft,
    build_drama_normalized_script_model,
    build_reference_units_step1_model,
)
from lib.speech_rate import project_speech_rate_override
from lib.text_backends.base import DEFAULT_MAX_OUTPUT_TOKENS, TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
from lib.text_utils import strip_json_code_fences
from server.agent_runtime.sdk_tools._context import (
    MAX_INSTRUCTIONS_LEN,
    ToolContext,
    fetch_video_caps,
    read_instructions_arg,
    reference_unit_duration_tiers,
    resolve_video_caps,
    tool_error,
)

# 四个分集数据生成工具共用的 instructions 参数 schema：用户意见原样注入 prompt 末尾的
# 「用户意见」分节，遵循强度由正文表达（需要强约束时在正文写明）。
_INSTRUCTIONS_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "用户对本次生成的意见原文（可选）；原样注入 prompt 末尾的「用户意见」分节，"
        f"遵循强度由正文表达，缺省/空白视同未传，最长 {MAX_INSTRUCTIONS_LEN} 字符"
    ),
}

logger = logging.getLogger(__name__)


def _parse_step1_json(response_text: str, model: type[BaseModel], *, label: str, top_shape: str) -> dict:
    """解析并校验 step1 结构化响应为 dict；校验失败 fail-loud 抛 ValueError，不返回未校验内容。

    ``model`` 取自调用处用 ``supported_durations`` 构造的同一份动态 schema（即 response_schema），
    令本地校验与 response_schema 同口径：即使 backend 未严格执行 schema，超出 supported_durations
    的时长、缺字段也在此被拦截。校验失败抛错而非降级保留原始 JSON——否则未校验内容会被当成正式
    step1 文件落盘（下游读取仅守最外层形状、放行），把非法时长 / 缺字段拖到 step2 或最终
    save_script 才暴露。与 narration 的 _load_narration_step1 严格读取同口径：只有经 schema
    校验的内容才成为持久化的 step1 真值源。
    """
    text = strip_json_code_fences(response_text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} JSON 解析失败: {e}")
    if not isinstance(data, dict):
        raise ValueError(f"{label}结构异常：顶层应为对象 {top_shape}")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as e:
        raise ValueError(f"{label}结构校验失败: {e}") from e


def _parse_normalized_content(response_text: str, model: type[BaseModel]) -> dict:
    """drama step1（normalize）响应解析：见 ``_parse_step1_json``。"""
    return _parse_step1_json(response_text, model, label="step1 规范化内容", top_shape="{title, scenes}")


def _load_novel_source(project_path: Path, source: str | None) -> str:
    """读取 step1 工具的源文：指定 source 文件或 ``source/`` 目录全部文本；异常情况抛 ValueError。

    normalize / split 两类 step1 工具共用：路径越界、文件缺失、目录为空、内容为空均 fail-fast，
    调用方把消息包装为工具错误信封。

    ``source`` 除工具自己产出外，也会被 ``revalidate_reference_step1_draft`` 传入隔离草稿的
    ``meta.source``——那是 agent 可编辑的 JSON 字段，类型标注管不住运行时值。非 str/None 时
    直接抛 ValueError 而非让它落进 ``safe_join``：那里对非路径类型是 ``TypeError``，本函数
    的调用方一律只接 ValueError，放行 TypeError 会在 web 审核 gate 的读时重算里变成未处理的
    500，而不是「无法重算」这个本该有的降级态。
    """
    if source is not None and not isinstance(source, str):
        raise ValueError(f"meta.source 类型非法，须为字符串或 null：{source!r}")
    if source:
        try:
            source_path = safe_join(project_path, source)
        except PathTraversalError as exc:
            raise ValueError(f"路径超出项目目录: {source}") from exc
        if not source_path.is_file():
            # 存在但不是文件（如指向目录）同样按「未找到源文件」处理：直接 read_text() 对目录
            # 会抛 IsADirectoryError，落进本函数调用方一律只接的 ValueError 之外，在 web 审核
            # gate 的读时重算里会变成未处理的 500。
            raise ValueError(f"未找到源文件: {source_path}")
        novel_text = source_path.read_text(encoding="utf-8")
    else:
        source_dir = project_path / "source"
        if not source_dir.exists() or not any(source_dir.iterdir()):
            raise ValueError(f"source/ 目录为空或不存在: {source_dir}")
        texts = [
            f.read_text(encoding="utf-8")
            for f in sorted(source_dir.iterdir())
            if f.is_file() and f.suffix in (".txt", ".md", ".text")
        ]
        novel_text = "\n\n".join(texts)
    if not novel_text.strip():
        raise ValueError("小说原文为空")
    return novel_text


# ---------------------------------------------------------------------------
# get_video_capabilities
# ---------------------------------------------------------------------------


async def _resolve_video_capabilities(project_name: str) -> dict[str, Any]:
    resolver = ConfigResolver(async_session_factory)
    return await resolver.video_capabilities(project_name)


async def _annotate_reference_unit_tiers(payload: dict[str, Any], project: dict[str, Any]) -> None:
    """就地补上参考视频路径逐 unit 的两套生效档位（非该路径的项目不补）。

    ``supported_durations`` 是型号声明的全集，不含「分辨率↔时长」「参考图↔时长」两条联动约束。
    参考路径的 unit 时长就是发给供应商的那个值，手工改 step1 时照全集取值会写出执行期申请不到
    的秒数——故把服务端拆分工具用的同两套档位一并返回，让改写方与生成方对同一份数字。

    ad 例外：该路径的 unit 是从 ``shots[]`` 派生的轻量索引、镜头时长不受档位枚举管辖，返回这
    两套档位只会诱导按剧集路径的口径去改 ad 镜头。
    """
    if payload.get("generation_mode") != "reference_video" or payload.get("content_mode") == "ad":
        return
    durations = [int(d) for d in payload.get("supported_durations") or []]
    if not durations:
        return
    with_refs, without_refs = await reference_unit_duration_tiers(project, payload, durations)
    payload["reference_unit_durations"] = {
        "with_references": with_refs,
        "without_references": without_refs,
    }


def get_video_capabilities_tool(ctx: ToolContext):
    @tool(
        "get_video_capabilities",
        "查视频模型能力（model 粒度）+ 用户项目偏好。返回 JSON；"
        "参考生视频项目另含 reference_unit_durations（按 unit 有无 @ 引用分开的两套生效档位）。"
        "能力按项目生成路线定轴，全项目同一口径，无需指定剧集。",
        {"type": "object", "properties": {}},
    )
    async def _handler(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = await _resolve_video_capabilities(ctx.project_name)
            await _annotate_reference_unit_tiers(payload, ctx.pm.load_project(ctx.project_name))
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


def _uses_reference_video_units(project_data: dict[str, Any]) -> bool:
    """项目是否产出参考视频 unit——隔离草稿只在这条路径上有意义。

    ad 的 unit 是 shots 的派生索引、无 step1 拆分，即使走参考路线也不在此列。
    """
    if project_data.get("content_mode", "narration") == "ad":
        return False
    return is_reference_video_project(project_data)


def _resolve_step1_path(project_path: Path, episode: int, project_data: dict[str, Any]) -> tuple[Path, str] | None:
    """Return (step1_md path, hint text for missing-file error)；ad 一键生成不依赖 step1，返回 None。"""
    content_mode = project_data.get("content_mode", "narration")
    if content_mode == "ad":
        # ad 创作输入是 project.json 的 brief + 产品信息 + target_duration，
        # ScriptGenerator 的 ad 分支不读 drafts/ 中间文件。
        return None
    generation_mode = project_data.get("generation_mode")
    drafts_path = episode_drafts_dir(project_path, episode)
    if generation_mode == "reference_video":
        # reference_video 生成需结构化 step1 JSON；仅存旧版 .md 时给出与
        # ScriptGenerator._load_reference_step1 一致的重拆迁移提示，而非笼统的缺文件错误。
        rv_json = drafts_path / REFERENCE_VIDEO_STEP1_FILENAME
        if not rv_json.exists() and (drafts_path / REFERENCE_VIDEO_STEP1_LEGACY_FILENAME).exists():
            return rv_json, (
                f"重跑 split-reference-video-units 把旧 {REFERENCE_VIDEO_STEP1_LEGACY_FILENAME} "
                f"重新拆分为结构化 {REFERENCE_VIDEO_STEP1_FILENAME}"
            )
        return rv_json, "split-reference-video-units subagent (Step 1)"
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
                "instructions": _INSTRUCTIONS_SCHEMA,
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            dry_run = bool(args.get("dry_run"))
            instructions, param_err = read_instructions_arg(args)
            if param_err is not None:
                return param_err

            project_path = ctx.project_path
            try:
                project_data = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                project_data = {}

            # 隔离草稿在场先于「缺 step1」与审核 gate 报出。三者都判「未放行」，但出路各不相同：
            # 首次拆分就违约时正式 step1 本就不存在，先报缺文件会把 agent 引回重跑拆分——正是本
            # 机制要避免的「丢弃重抽」；gate 阻塞则要用户去 Web 端确认，agent 自己解决不了。
            # 只在项目实际走参考路径时判：分镜路线项目上残留的隔离草稿与其生成路径无关，
            # 非参考路径的写入方不会清它们，无条件判会把该集永久卡死。
            if _uses_reference_video_units(project_data):
                for kind in (QUARANTINE_KIND_STEP1, QUARANTINE_KIND_STEP2):
                    if quarantine_exists(project_path, episode, kind):
                        path = quarantine_path(project_path, episode, kind)
                        return {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"⏸️ 本集有违约产物待处置（{path}），step2 视觉生成已中止。"
                                        f"请按草稿内 violations 的定位修正 content，再调用 {PROMOTE_TOOL_NAME} 晋升。"
                                    ),
                                }
                            ],
                            "is_error": True,
                        }

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
                prompt = await generator.build_prompt(episode, instructions=instructions)
                return {
                    "content": [{"type": "text", "text": f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}"}]
                }

            # step1→step2 审核 gate：drama / narration / reference_video 的结构化 step1 中间态须经
            # web 显式确认才放行 step2 视觉生成；未确认（或确认后内容又被改）时阻塞，引导用户先在
            # Web 端审阅确认。ad（无 step1）不适用，gate 自动放行。
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
            result_path = await generator.generate(episode=episode, instructions=instructions)
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
        "确认本集 step1 结构化中间态（drama / narration 的逐字口播 / 原文，reference_video 的 "
        "video_unit 拆分），放行 step2 视觉生成。仅在用户对话中明确同意进入视觉生成、或已在 Web 端审阅"
        "认可后调用——这是 step2 的显式确认动作，与 Web 端确认等价；未确认时 generate_episode_script "
        "会被审核 gate 阻塞。",
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
                state = await service.confirm(ctx.project_name, episode)
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


async def _fetch_caps_with_fallback(project: dict[str, Any], episode: int) -> tuple[int | None, list[int]]:
    """Script normalization is best-effort: prompt生成 不该被能力查询失败堵住。

    Soft-fallbacks to ``duration_presets.DEFAULT_FALLBACK`` so the LLM still
    receives a usable duration constraint set if the resolver hiccups —— 与
    自定义供应商写入层的保守默认同一真相源，避免软回退口径含供应商未必支持的时长。

    时长已按项目分辨率经联动约束收窄。参考图约束不在此施加：走参考生视频的项目 step1 用
    ``split_reference_video_units``（见 ``_fetch_reference_caps_with_fallback``），本 helper
    服务的 drama normalize / narration 拆分两个工具按分工不服务该路径。

    ``default_duration`` 非返回集合成员时按 None 处理（即回到「auto」档，由模型按内容节奏选）：
    项目存的是用户配置的原样值，收窄后（或软回退到 ``DEFAULT_FALLBACK`` 后）它可能落在集合外，
    而 ``build_normalize_prompt`` 对非成员 default 是 fail-loud 的——不归 None 会把「已保存的
    越界默认时长」变成整个工具的硬失败。与 ``_fetch_reference_caps_with_fallback`` 同口径。
    """
    try:
        default_int, durations = await fetch_video_caps(project, generation_mode=None)
    except (FileNotFoundError, ValueError) as exc:
        logger.info("video_capabilities 不可解析，使用 fallback %s：%s", DEFAULT_FALLBACK, exc)
        return None, list(DEFAULT_FALLBACK)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_capabilities 查询异常，使用 fallback %s：%s", DEFAULT_FALLBACK, exc)
        return None, list(DEFAULT_FALLBACK)
    if not durations:
        durations = list(DEFAULT_FALLBACK)
    if default_int is not None and default_int not in durations:
        default_int = None
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
                "instructions": _INSTRUCTIONS_SCHEMA,
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
            instructions, param_err = read_instructions_arg(args)
            if param_err is not None:
                return param_err

            project_path = ctx.project_path
            project = ctx.pm.load_project(ctx.project_name)

            try:
                novel_text = _load_novel_source(project_path, source)
            except ValueError as exc:
                return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

            default_duration, supported_durations = await _fetch_caps_with_fallback(project, episode)
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
                # 每场选不低于该场 utterances 口播时长的档位，语速按此从 lib.speech_rate 单一真相源注入
                # （项目级覆盖优先于语言默认）。
                source_language=project.get("source_language"),
                speech_rate_override=project_speech_rate_override(project),
            )
            prompt = append_user_instructions(prompt, instructions)

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


# ---------------------------------------------------------------------------
# split_reference_video_units
# ---------------------------------------------------------------------------


class ReferenceSplitCaps(NamedTuple):
    """rv 拆分用的视频能力：两套逐 unit 档位 + 派生上限 + 用户偏好 + 声音输入档。

    ``reference_durations`` / ``text_durations`` 是带 / 不带 ``@`` 引用的 unit 各自的生效档位，
    ``durations`` 是二者的并集——schema 枚举与 prompt 候选集合取并集，因为落在任一套内的时长都
    可能合法；归属哪一套要等正文派生出 references 才知道。三者相等即该型号在当前分辨率下未声明
    生效的「参考图↔时长」联动约束，多数型号如此。

    ``voice`` 是同一次能力解析派生出的声音输入档，供声音相关的容忍 warning 消费——与时长档位同源
    于这一次解析，分两次查会让同一份产物的档位与声音提示描述不同时刻的配置。能力解析故障回退时
    档位相关的几位落到 ``VoiceRenderSettings`` 的字段默认，唯 ``requested_generate_audio`` 仍带着
    本集的无声意图（该位不依赖能力接口，回退分支独立解析后写回，见
    ``_fetch_reference_caps_with_fallback``）。携带值对象而非原始能力 dict：下游只需要声音那几位，
    穿一整个 dict 过接口会把能力 key 名耦合扩散到消费侧。
    """

    default_duration: int | None
    durations: list[int]
    reference_durations: list[int]
    text_durations: list[int]
    max_duration: int
    max_refs: int | None
    voice: VoiceRenderSettings

    def tiers_for(self, *, has_references: bool) -> list[int]:
        """该引用状态下的生效档位。"""
        return self.reference_durations if has_references else self.text_durations


async def _fetch_reference_caps_with_fallback(project: dict[str, Any], episode: int) -> ReferenceSplitCaps:
    """解析 rv 拆分所需的视频能力（见 ``ReferenceSplitCaps``）。

    与 ``_fetch_caps_with_fallback`` 同口径 best-effort：resolver 故障时回退
    ``duration_presets.DEFAULT_FALLBACK``、``max_refs`` 视为未声明。

    unit 是一次生成调用的单元，拆分阶段定的时长就是真正发给供应商的那个值，故档位取**经时长
    联动约束收窄后**的集合：不收窄的话（海螺 1080p 只接受 6 秒）step1 会按全集拆出超标的 unit，
    step2 的枚举 schema 再把它判非法。

    收窄逐 unit 分两套（``reference_unit_duration_tiers``）：「参考图↔时长」约束只对真的带参考图
    的请求生效，整集一律按带图收窄会把无引用 unit 本可申请的短档也收掉。schema 枚举与 prompt
    候选取两套的并集——落在任一套内的时长都可能合法，具体归属由该 unit 的 references 决定，
    在正文派生出 references 之后逐 unit 判（见 ``_build_reference_units_from_flat``）。
    ``max_duration`` 随之是并集的最大值。
    ``default_duration`` 非并集成员（用户配置漂移）按 None 处理，避免 prompt 自相矛盾。
    """
    try:
        caps = await resolve_video_caps(project)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_capabilities 查询异常，使用 fallback %s：%s", DEFAULT_FALLBACK, exc)
        caps = {}
        # requested_generate_audio 不依赖能力接口（见 generation_context.py 同名字段注释），
        # 能力解析失败也不能连带丢失，否则本该报的 WARN_SILENT_EPISODE 会静默消失。
        try:
            resolver = ConfigResolver(async_session_factory)
            caps["requested_generate_audio"] = await resolver.video_generate_audio_for_project(project)
        except Exception as inner_exc:  # noqa: BLE001
            # 与其余能力字段的「不明时不额外收紧」相反：这里不明时收紧到 False——静默丢掉
            # 一次声音提示，好过在双重解析失败时把用户的无声意图错读成有声。
            logger.warning("video_generate_audio 独立解析也失败，声音提示按无声降级：%s", inner_exc)
            caps["requested_generate_audio"] = False
    durations = [int(d) for d in caps.get("supported_durations") or []]
    if not durations:
        durations = list(DEFAULT_FALLBACK)
    with_refs, without_refs = await reference_unit_duration_tiers(project, caps, durations)
    unit_durations = sorted(set(with_refs) | set(without_refs))
    max_duration = max(unit_durations)
    raw_refs = caps.get("max_reference_images")
    max_refs = int(raw_refs) if isinstance(raw_refs, int | float) else None
    raw_default = caps.get("default_duration")
    default = int(raw_default) if isinstance(raw_default, int | float) else None
    if default is not None and default not in unit_durations:
        default = None
    return ReferenceSplitCaps(
        default_duration=default,
        durations=unit_durations,
        reference_durations=sorted(set(with_refs)),
        text_durations=sorted(set(without_refs)),
        max_duration=max_duration,
        max_refs=max_refs,
        voice=VoiceRenderSettings.from_caps(caps),
    )


def _validate_unit_duration_tier(label: str, duration: int, *, has_references: bool, caps: ReferenceSplitCaps) -> None:
    """按该 unit 的引用状态判时长是否落在生效档位内，出档抛 ``DraftViolation``。

    schema 的枚举卡的是两套档位的并集，一个带引用的 unit 因此仍可能取到只有无引用 unit 才
    合法的秒数——那样的 unit 执行期申请不到，等到入队才失败已无统一纠正入口。错误消息给出
    两条出路（换档位 / 去引用），与 prompt 里的教学同一口径。

    抛的是内容违约而非 ``ValueError``：这一类同样是 agent 改一改草稿就能修好的，走隔离草稿
    的修复闭环，不该退回丢弃重抽。
    """
    tiers = caps.tiers_for(has_references=has_references)
    if duration in tiers:
        return
    state = "带 `@` 资产引用" if has_references else "无 `@` 资产引用"
    remedy = (
        "；请改取该档位内的时长，或把次要资产融入描述文字、不用 `@` 引用"
        if has_references
        else "；请改取该档位内的时长"
    )
    raise DraftViolation(
        f"{label} 时长 {duration}s 不在{state}的 unit 的生效档位 {tiers} 内{remedy}",
        code="duration_off_tier",
        label=label,
    )


def _collect_reference_flat_violations(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    episode: int,
    novel_text: str,
    caps: ReferenceSplitCaps,
    source_language: str | None,
) -> list[DraftViolation]:
    """逐 unit 收齐 step1 扁平产出的全部违约（不在首个违约处中断）。

    schema 已卡死时长枚举与外层形状；此处补依赖运行时能力值 / 项目登记表 / 源文的约束——
    时长落在该 unit 引用状态对应的生效档位内、原文锚是源文逐字子串、正文语法与资产引用合法、
    台词量念得完。收齐而非首个即抛：报告要能一次列全所有坏 unit，否则 agent 每修一处就要再跑
    一轮才知道下一处。

    时长档位与正文合并为一个入口：适用哪套档位取决于该 unit 有没有 references，而 references
    正是从正文机械派生的——正文解析不出时无从判档位，此时报出的也只会是同一个问题的另一种说法。
    """
    # 台词口播量的语速与 prompt 侧同源：项目级覆盖优先，否则按语言默认。
    speech_rate_override = project_speech_rate_override(project)
    violations: list[DraftViolation] = []
    for index, flat in enumerate(flat_units, start=1):
        label = f"unit E{episode}U{index:02d}"
        duration = flat["duration_seconds"]
        source_text = flat["source_text"]
        text = flat["text"]

        def _check_text_and_tier(la: str = label, tx: str = text, d: int = duration) -> None:
            _shots, refs = validate_unit_text(la, tx, project, max_refs=caps.max_refs)
            _validate_unit_duration_tier(la, d, has_references=bool(refs), caps=caps)

        violations.extend(
            collect_violations(
                [
                    lambda la=label, st=source_text: validate_source_text_anchor(la, st, novel_text),
                    _check_text_and_tier,
                    lambda la=label, tx=text, d=duration: validate_dialogue_load(
                        la, tx, d, source_language, speech_rate_override
                    ),
                ]
            )
        )
    return violations


def _build_reference_units_from_flat(
    flat_units: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    episode: int,
    max_refs: int | None,
) -> list[dict]:
    """把已校验通过的扁平产出派生为落盘的结构化 unit 表。

    LLM 只写内容，机器写结构：``unit_id`` 按数组序号编号、``shots`` 由 ``镜头N：`` 切分、
    ``references`` 由 ``@[名称]`` 派生（首现顺序即参考图编号），三者都没有让模型写错的余地。
    调用方须先经 ``_collect_reference_flat_violations`` 确认无违约——本函数直接复用同一个
    ``validate_unit_text`` 取派生结果，校验与派生因此同一次解析，不会「校验看到的文本」与
    「落盘的 references」出自两套口径。
    """
    units: list[dict] = []
    for index, flat in enumerate(flat_units, start=1):
        unit_id = f"E{episode}U{index:02d}"
        shots, refs = validate_unit_text(f"unit {unit_id}", flat["text"], project, max_refs=max_refs)
        units.append(
            {
                "unit_id": unit_id,
                "shots": [s.model_dump() for s in shots],
                "duration_seconds": flat["duration_seconds"],
                "references": [r.model_dump() for r in refs],
                "source_text": flat["source_text"],
            }
        )
    return units


#: 落盘照常、只随产物呈现的容忍 warning（声音降级）。其余 warning 键（未登记 mention /
#: 说话人、语法误用）在机器产物这条路上是阻断违约，不走容忍分支。
_TOLERATED_VOICE_WARNINGS = (
    WARN_SPEAKER_WITHOUT_AUDIO,
    WARN_REFERENCE_AUDIO_OVERFLOW,
    WARN_SILENT_MODEL,
    WARN_SILENT_EPISODE,
)


def _reference_voice_warning_lines(
    unit_texts: list[str], project: dict[str, Any], voice: VoiceRenderSettings
) -> list[str]:
    """逐 unit 派生声音绑定，取容忍类 warning 的渲染文本（跨 unit 去重、保持首现顺序）。

    逐 unit 而非把全集正文拼起来判：unit 就是一次生成调用，参考音频段数上限按调用计——拼起来
    判会把「每个 unit 各两个说话人」误报成超限。与编辑器预览、执行期渲染共用
    ``derive_voice_bindings``，三处对同一份文稿给出的声音结论因此不会分叉。

    ``requires_reference_image`` 在本处一律关掉：该位的判定要配 ``speakers_with_reference_image``
    才有意义，而拆分阶段的 unit 尚未确定随请求发出的参考图。开着而不给图集合，等于把每个说话人
    都判成「无画面可挂」，那条 warning 又不在容忍列表内会被丢弃——结果是「未设参考音频」「超出
    段数上限」这些该让 agent 看见的提示反被吞掉。
    """
    characters = project.get(BUCKET_KEY["character"]) or {}
    settings = replace(voice, requires_reference_image=False)
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for text in unit_texts:
        shots, _mentions = parse_prompt(text)
        utterances, _syntax_warnings = derive_utterances(shots)
        bindings = derive_voice_bindings(utterances, characters, settings)
        for warning in bindings.warnings:
            key = str(warning["key"])
            if key not in _TOLERATED_VOICE_WARNINGS:
                continue
            rendered = translate(key, **warning["params"])
            if (key, rendered) in seen:
                continue
            seen.add((key, rendered))
            lines.append(rendered)
    return lines


def _reference_result_text(step1_path: Path, units: list[dict], warning_lines: list[str], *, action: str) -> str:
    """晋升 / 拆分成功后回给 agent 的摘要：落盘统计 + 三类容忍 warning。

    ``action`` 点明这份正式 step1 是重新拆分还是草稿晋升来的：两条路都写同一个文件，摘要不分
    的话，agent 修完草稿会收到一句「拆分已保存」，读起来像它的修改被一次重抽覆盖了。

    warning 不阻断落盘，但必须随产物呈现——「角色没配参考音频」这类降级只在生成后才听得出来，
    不在产出当时说，agent 与用户都不会知道声音一致性已经打了折。
    """
    shot_count = sum(len(u.get("shots") or []) for u in units)
    total_seconds = sum(int(u.get("duration_seconds") or 0) for u in units)
    max_unit_refs = max(len(u.get("references") or []) for u in units)
    text = (
        f"✅ 参考视频单元{action}（结构化 step1）已保存: {step1_path}\n"
        f"📊 生成统计: {len(units)} 个 unit / {shot_count} 个 shot，"
        f"总时长 {total_seconds} 秒；单 unit references 最多 {max_unit_refs} 个"
    )
    if warning_lines:
        text += "\n⚠️ 声音降级提示（不阻断，产物已落盘）:\n" + "\n".join(f"- {line}" for line in warning_lines)
    return text


class ReferenceDraftRevalidation(NamedTuple):
    """step1 隔离草稿读时重判的结果。

    ``schema_failed`` 显式区分两个阶段：True 表示草稿连产出时的 schema 都没过（``flat_units``
    必为空，调用方只能按 ``draft.content`` 原样呈现）；False 时 ``flat_units`` 是收编后的扁平
    产出，``violations`` 为空即可晋升。两者的处置不同（原样 vs 收编），故不靠 ``flat_units``
    是否为空来反推。
    """

    violations: list[DraftViolation]
    flat_units: list[dict[str, Any]]
    caps: ReferenceSplitCaps
    schema_failed: bool


async def revalidate_reference_step1_draft(
    project_path: Path, project: dict[str, Any], episode: int, draft: QuarantinedDraft
) -> ReferenceDraftRevalidation:
    """按产出时那套校验器全量重判 step1 隔离草稿，只读、不写盘、不清草稿。

    重判走的是拆分工具用的同一个函数（``_collect_reference_flat_violations``），不是它的简化
    副本：晋升口径、web 审核 gate 的读时重算与产出口径必须同一份代码，否则「这里放行、下次
    生成时被拒」这类分叉会重新出现。能力与源文都重新解析——隔离期间用户可能改过模型配置或
    源文，重判要对着现值判。

    不依赖 ``ToolContext``（``project_path`` / ``project`` 由调用方传入而非从 ctx 派生）：
    web 审核 gate 的读时重算（``server/services/script_review.py``）没有 agent 工具的 ctx，
    只有 ``ProjectManager``；两处共用本函数而不各自加载 project，调用方各自加载一次即可。

    ``meta.source`` 缺失（草稿被改坏、无从重判）时抛 ``ValueError``。
    """
    # meta.source 记的是产出时的源文范围。缺键说明 meta 被改坏了：不能默默按整个 source/ 重解析
    # ——那比产出时更松，一份从别集抄来的原文锚会恰好命中而被放行。
    if "source" not in draft.meta:
        raise ValueError(
            f"隔离草稿 {draft.path} 的 meta.source 缺失（产出时记录的源文范围）；"
            "请恢复该字段（指定源文时为其相对路径，按整个 source/ 产出时为 null）后重试"
        )
    # 源文可能达数百 KB（整个 source/ 目录拼接），同步读盘直接放在这个 async 函数体里会占用
    # 事件循环——晋升工具走的是独立会话线程不敏感，但 web 审核 gate 的读时重算（同一份代码）
    # 在请求协程里跑，卸到线程避免拖慢并发的其它请求。
    novel_text = await asyncio.to_thread(_load_novel_source, project_path, draft.meta["source"])
    split_caps = await _fetch_reference_caps_with_fallback(project, episode)

    # 手改过的草稿先过产出时那份 schema：拆分侧由 response_schema 与 _parse_step1_json 卡住时长
    # 枚举与字段非空，晋升侧漏掉这一层的话，把 duration_seconds 改成非档位值、或整个删掉（收成
    # 0 秒）都能一路晋升进正式文件——正是本机制要防的「正式文件被污染」。schema 违约在这条路上
    # 没有 backend 可重试（内容是 agent 写的），故同样回报告让它继续改。
    #
    # 外层形状（units 缺失 / 不是数组 / 空数组）与逐 unit 的字段违约走同一条报告路径：两者都是
    # agent 编辑草稿时会犯的错，只有后者刷新报告的话，前者就把它甩出了「改完再晋升」的循环。
    raw_units = draft.content.get("units")
    schema = build_reference_units_step1_model(split_caps.durations)
    violations: list[DraftViolation] = []
    flat_units: list[dict[str, Any]] = []
    if not isinstance(raw_units, list) or not raw_units:
        violations = [
            DraftViolation(
                f"隔离草稿的 content.units 必须是非空的 unit 对象数组（当前为 {type(raw_units).__name__}）",
                code="schema_invalid",
            )
        ]
    else:
        try:
            flat_units = schema.model_validate({"units": raw_units}).model_dump()["units"]
        except ValidationError as exc:
            violations = [
                DraftViolation(
                    f"隔离草稿的 content 不符合 step1 产出结构：{exc}；"
                    f"每个 unit 须有非空 source_text / text，且 duration_seconds 取自模型档位 {split_caps.durations}",
                    code="schema_invalid",
                )
            ]
    if violations:
        return ReferenceDraftRevalidation(violations, [], split_caps, schema_failed=True)

    source_language = project.get("source_language")
    violations = _collect_reference_flat_violations(
        flat_units,
        project,
        episode=episode,
        novel_text=novel_text,
        caps=split_caps,
        source_language=source_language,
    )
    return ReferenceDraftRevalidation(violations, flat_units, split_caps, schema_failed=False)


async def _promote_reference_step1(ctx: ToolContext, episode: int, draft: QuarantinedDraft) -> dict[str, Any]:
    """按产出时那套校验器全量重判 step1 隔离草稿，通过则晋升为正式 step1 并清除草稿。"""
    project_path = ctx.project_path
    project = ctx.pm.load_project(ctx.project_name)
    revalidation = await revalidate_reference_step1_draft(project_path, project, episode, draft)
    violations, flat_units, split_caps = revalidation.violations, revalidation.flat_units, revalidation.caps
    if revalidation.schema_failed:
        # schema 违约：写回 agent 手里那份原样内容，不做收编——字段被改坏时收编会把它的原稿
        # 改形，它照着报告回去看反而对不上自己写的东西。
        report = quarantine_and_report(
            project_path,
            episode,
            QUARANTINE_KIND_STEP1,
            content=draft.content,
            violations=violations,
            meta=draft.meta,
        )
        return {"content": [{"type": "text", "text": report}], "is_error": True}
    if violations:
        report = quarantine_and_report(
            project_path,
            episode,
            QUARANTINE_KIND_STEP1,
            content={"units": flat_units},
            violations=violations,
            meta=draft.meta,
        )
        return {"content": [{"type": "text", "text": report}], "is_error": True}

    units = _build_reference_units_from_flat(flat_units, project, episode=episode, max_refs=split_caps.max_refs)
    # 写盘经单一出口（lib.script_review.write_step1_locked）：锁、基线比对、step2 隔离草稿清理
    # 只存在那一处。基线指纹取自取回 / 隔离时记进 meta 的 base_fingerprint——正式文件在草稿
    # 产出后被其他写入方（Web 端保存、另一次拆分）改过时晋升中止、返回冲突报告让 agent 合并，
    # 不静默覆盖对方的修改。引入基线前产出的存量草稿缺该键，按无基线晋升。
    expected = (
        draft.meta["base_fingerprint"] if "base_fingerprint" in draft.meta else script_review.UNCHECKED_FINGERPRINT
    )
    try:
        with script_review.step1_write_lock(project_path, episode) as step1_path:
            script_review.write_step1_locked(project_path, episode, {"units": units}, expected_fingerprint=expected)
            # 落盘成功后才清草稿：写盘失败（含冲突）时草稿还在，改完重试晋升即可，不会两头皆空。
            # 清理与写盘同一临界区：并发的取回请求不会在两步之间看到「正式文件已是新内容、
            # 草稿却还在场」的中间态。
            clear_quarantine(project_path, episode, QUARANTINE_KIND_STEP1)
    except script_review.Step1WriteConflict as conflict:
        return {
            "content": [{"type": "text", "text": _render_step1_conflict_report(episode, draft, conflict)}],
            "is_error": True,
        }
    warning_lines = _reference_voice_warning_lines([f["text"] for f in flat_units], project, split_caps.voice)
    return {
        "content": [{"type": "text", "text": _reference_result_text(step1_path, units, warning_lines, action="晋升")}]
    }


def _render_step1_conflict_report(
    episode: int, draft: QuarantinedDraft, conflict: script_review.Step1WriteConflict
) -> str:
    """渲染晋升遇乐观并发冲突时回给 agent 的结构化报告：最新内容 + 合并指引。

    报告要让编辑方能就地合并：附上盘上现值的扁平书写层（与草稿 ``content`` 同形，可逐 unit
    对照），并指明确认合并后把 ``meta.base_fingerprint`` 更新为现值指纹——这一步是显式确认
    「我已看过并合并了对方的修改」，之后重新晋升才会放行；不更新就重试只会拿到同一份报告。
    """
    current_units = (conflict.current_content or {}).get("units")
    if isinstance(current_units, list) and current_units:
        latest = json.dumps({"units": _flatten_reference_step1_units(current_units)}, ensure_ascii=False, indent=2)
        latest_block = f"当前正式 step1 的最新内容（扁平书写层，与草稿 content 同形）：\n{latest}"
    else:
        latest_block = "当前正式文件不存在或不是合法的 step1 JSON，无法附上最新内容；请自行读取该文件确认。"
    # 指纹按 JSON 字面量给：正式文件已被删除时现值是 null，写成 "None" 会让 agent 把这串字符
    # 当基线填回 meta，之后每次重晋升都比对不上、拿到同一份报告，冲突再也解不掉。
    actual_literal = json.dumps(conflict.actual)
    return (
        "❌ 晋升中止（并发冲突）：正式 step1 在本草稿产出后已被其他写入方（如 Web 端保存）修改，"
        "直接晋升会覆盖对方的修改，本次未写盘、草稿仍在场。\n"
        f"草稿基线指纹: {json.dumps(conflict.expected)}；盘上现值指纹: {actual_literal}\n\n"
        f"{latest_block}\n\n"
        f"处置：对照上方最新内容与草稿 {draft.path} 的 content.units，把对方的修改合并进草稿；"
        f"合并完成后把草稿 meta.base_fingerprint 更新为 {actual_literal}，"
        f'再调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 重新晋升。'
    )


def _flatten_reference_step1_units(units: list[Any]) -> list[dict[str, Any]]:
    """正式 step1 的结构化 unit 表 → 隔离草稿装的扁平书写层（``_build_reference_units_from_flat`` 的逆向）。

    ``unit_id`` / ``shots`` / ``references`` 不进草稿：它们是机器派生物，草稿是给 agent 改的那
    一层，带上派生字段等于给漂移开口子（改了 references、晋升时又按正文重新派生覆盖）。

    ``text`` 经 ``render_shots_text`` 还原 ``镜头N：`` header——落盘的 ``shots[*].text`` 不带
    header，裸拼接后晋升时 ``parse_prompt`` 找不到 header，整个 unit 会塌成一个镜头。

    盘上 unit 不合形状时不 fail-loud：字段缺失或类型不符时**原样带过**（缺失填 None / 空串），
    交由晋升侧的 schema 重判逐条报告给 agent。原样带过而非归一化成合法值：``8.0`` 被改写成
    ``0`` 后，agent 从草稿里看到的是一个它没写过的时长，报告说「时长不在档位内」也对不上盘
    上的原值——保留原值，让它自己看见错在哪。非 dict 的 unit 同样不丢弃：填空占位保留在数组
    对应位置，让晋升侧 schema 判它「结构非法」逐条报出——直接跳过会让数组变短，若剩余 unit
    恰好都能过校验，晋升会悄悄覆盖正式文件、丢失这个 unit 而无人知晓。

    render 后用 ``parse_prompt`` 重新解析校验分镜数不变：某个 shot 自身内容里若恰好有一行
    形如「镜头N：」（旧数据经 Web 端保存，字段本身不禁止这种文本），加了 header 的首行会跟
    这行撞在一起，解析回去时会被误判成新的镜头边界，一个 shot 悄悄拆成两个——agent 明明没
    编辑这个 unit，原样晋升也会带着错位的分镜覆盖正式文件。分镜数对不上时同样清空为占位，
    交给 schema 判非法。
    """
    flat: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            flat.append({"duration_seconds": None, "source_text": "", "text": ""})
            continue
        shots = unit.get("shots")
        shots_list = shots if isinstance(shots, list) else []
        text = render_shots_text(shots_list)
        if len(parse_prompt(text)[0]) != len(shots_list):
            text = ""
        flat.append(
            {
                "duration_seconds": unit.get("duration_seconds"),
                "source_text": unit.get("source_text", ""),
                "text": text,
            }
        )
    return flat


def open_reference_step1_for_edit_tool(ctx: ToolContext):
    @tool(
        STEP1_EDIT_TOOL_NAME,
        "把本集已落盘的参考生视频 step1 拆分取回可编辑的隔离草稿（扁平书写层：时长 + 原文锚 + 正文），"
        f"用于修改已有拆分。改完调用 {PROMOTE_TOOL_NAME} 全量校验并晋升回正式文件。"
        "正式 step1 不可用 Write/Edit 直改——它与 Web 端保存、迁移、重拆分共享一把文件锁，"
        "只能经工具写盘。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {
                    "type": "string",
                    "description": (
                        "本集小说源文件路径（相对项目目录，如 source/episode_1.txt）；"
                        "晋升时按它重判原文锚，不传则按整个 source/ 目录重解析（判定更松）"
                    ),
                },
            },
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            source = args.get("source")
            project_path = ctx.project_path
            project_data = ctx.pm.load_project(ctx.project_name)

            # 与晋升工具同一判据：分镜路线项目上盘存的 step1 与其生成路径无关，
            # 取回来编辑只会诱导 agent 改一份不会被消费的文件。
            if not _uses_reference_video_units(project_data):
                return {
                    "content": [
                        {"type": "text", "text": f"❌ 第 {episode} 集当前不走参考生视频路径，无 step1 拆分可编辑"}
                    ],
                    "is_error": True,
                }

            # source 在写草稿前校验：草稿一旦落盘就把它记进 meta.source 供晋升重判用，若此刻
            # 是个缺失/改名/写错的路径，晋升会在 _load_novel_source 上反复报错，而草稿已在场
            # 又挡住重新取回改正 source——agent 会卡在一个自己改不动的死角。校验失败时不落盘，
            # 无效参数不留持久副作用。
            if source is not None:
                try:
                    _load_novel_source(project_path, source)
                except ValueError as exc:
                    return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

            # 草稿有无的检查、正式文件的读取、草稿的写入须在同一把锁的临界区内完成：拆开在锁外
            # 各做一次的话，同一集的两个并发取回请求可能都先看到「无草稿」，再都各自写入草稿，
            # 后写者悄悄覆盖前者的 content 与 meta.source。写临界区与 Web 端保存、迁移同一把锁，
            # 读也持锁避免取回一份写到一半的 step1。
            with script_review.step1_write_lock(project_path, episode) as step1_path:
                # 已有草稿在场时不覆盖：那份草稿要么是违约产物、要么是上一轮取回后 agent 已改了
                # 一半，拿正式文件盖过去等于抹掉它手上的修改。两种情况的出路相同——继续改那份
                # 草稿再晋升。
                if quarantine_exists(project_path, episode, QUARANTINE_KIND_STEP1):
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"❌ 第 {episode} 集已有 step1 隔离草稿在场："
                                    f"{quarantine_path(project_path, episode, QUARANTINE_KIND_STEP1)}\n"
                                    "不覆盖它（可能已含未晋升的修改）；请直接编辑该草稿的 content.units[i]，"
                                    f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 晋升。'
                                ),
                            }
                        ],
                        "is_error": True,
                    }

                data = load_json_or_none(step1_path)
                if not isinstance(data, dict):
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"❌ 第 {episode} 集没有可编辑的正式 step1（{step1_path} 不存在或不是合法 "
                                    "JSON）；首次生成请调用 split_reference_video_units"
                                ),
                            }
                        ],
                        "is_error": True,
                    }
                raw_units = data.get("units")
                if not isinstance(raw_units, list) or not raw_units:
                    return {
                        "content": [{"type": "text", "text": f"❌ {step1_path} 的 units 不是非空数组，无法取回编辑"}],
                        "is_error": True,
                    }

                draft_path = write_quarantine(
                    project_path,
                    episode,
                    QUARANTINE_KIND_STEP1,
                    content={"units": _flatten_reference_step1_units(raw_units)},
                    # 取回时无违约可报：草稿在这条路上是「编辑工位」而非「违约产物」，报告为空即可，
                    # 晋升时照常全量重判。
                    violations=[],
                    # source 键一律写出（未指定时为 null），与拆分侧同口径：晋升侧据此区分「本就按
                    # 整个 source/ 判锚」与「meta 被改坏」。base_fingerprint 记下此刻正式文件的
                    # 指纹（与本临界区读到的 data 同一份内容）：晋升前按它做基线比对，取回与晋升
                    # 之间正式文件被 Web 端保存等并发写入改过时中止晋升、报冲突让 agent 合并。
                    meta={
                        "source": source or None,
                        "base_fingerprint": script_review.content_fingerprint_of_data(data),
                    },
                )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"✅ 第 {episode} 集 step1 已取回可编辑草稿：{draft_path}\n"
                            f"📊 {len(raw_units)} 个 unit（正式文件 {step1_path} 保持原样，未改动）\n\n"
                            "编辑口径：改 content.units[i] 的 text / source_text / duration_seconds；"
                            "unit_id / shots / references 是派生物，不在草稿里、也不要手写。"
                            "增删 unit 即增删数组元素，unit_id 按新顺序重编。\n"
                            f'改完调用 {PROMOTE_TOOL_NAME}({{"episode": {episode}}}) 全量校验并晋升回正式文件；'
                            "违约时返回逐条报告，继续改再晋升，无轮次上限。\n"
                            "草稿在场期间审阅门与 step2 生成被阻塞；放弃修改就原样晋升（内容未变即等于回写原稿）。"
                        ),
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error(STEP1_EDIT_TOOL_NAME, exc)

    return _handler


def split_reference_video_units_tool(ctx: ToolContext):
    @tool(
        "split_reference_video_units",
        "把本集小说原文拆分为参考生视频 video_unit 表（unit → 时长 + 原文锚 + 书写层正文），"
        "保存到 drafts/episode_N/step1_reference_units.json，供 generate_episode_script"
        "（reference_video 模式）消费。unit_id / shots / references 由工具从正文机械派生，"
        "并校验原文锚、正文语法、资产引用与台词量。dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {
                    "type": "string",
                    "description": "指定小说源文件路径（相对项目目录）；默认读取 source/ 下所有文本",
                },
                "instructions": _INSTRUCTIONS_SCHEMA,
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
            instructions, param_err = read_instructions_arg(args)
            if param_err is not None:
                return param_err

            project_path = ctx.project_path
            project = ctx.pm.load_project(ctx.project_name)

            try:
                novel_text = _load_novel_source(project_path, source)
            except ValueError as exc:
                return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

            characters = project.get("characters")
            characters = characters if isinstance(characters, dict) else {}
            scenes = project.get("scenes")
            scenes = scenes if isinstance(scenes, dict) else {}
            props = project.get("props")
            props = props if isinstance(props, dict) else {}

            split_caps = await _fetch_reference_caps_with_fallback(project, episode)
            episode_outline, next_episode_outline = episode_outline_context(project, episode)
            prompt = build_reference_units_split_prompt(
                novel_text=novel_text,
                project_overview=project.get("overview", {}),
                characters=characters,
                scenes=scenes,
                props=props,
                supported_durations=split_caps.durations,
                reference_supported_durations=split_caps.reference_durations,
                text_supported_durations=split_caps.text_durations,
                max_duration=split_caps.max_duration,
                max_reference_images=split_caps.max_refs,
                default_duration=split_caps.default_duration,
                episode=episode,
                # 输出语言取项目 source_language（生成内容语言的唯一真相源），与 normalize 同口径。
                target_language=project.get("source_language") or "中文",
                # source_language（zh / en / vi 或 None）另供台词口播时长下界取语速（项目级覆盖优先），
                # 与后校验同一把尺。
                source_language=project.get("source_language"),
                speech_rate_override=project_speech_rate_override(project),
                # 分集大纲约束本集内容边界，同 drama step1。
                episode_outline=episode_outline,
                next_episode_outline=next_episode_outline,
            )
            prompt = append_user_instructions(prompt, instructions)

            if dry_run:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符",
                        }
                    ]
                }

            # 结构化输出：response_schema 按 supported_durations 卡死 unit 时长枚举，产出扁平
            # unit → 时长 + 原文锚 + 书写层正文；unit_id / shots / references 不进 LLM 输出、
            # 由下方机械派生（正文内的语法则由 parser 后校验兜底，schema 管不到）。
            schema = build_reference_units_step1_model(split_caps.durations)
            generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=ctx.project_name)
            result = await generator.generate(
                TextGenerationRequest(
                    prompt=prompt,
                    response_schema=schema,
                    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                ),
                project_name=ctx.project_name,
            )
            flat = _parse_step1_json(result.text, schema, label="step1 拆分内容", top_shape="{units}")

            # 已过 schema（_parse_step1_json 用的就是这份 response_schema），units 的字段与类型
            # 无需再收编；此处只守最外层形状。
            flat_units = flat.get("units")
            if not isinstance(flat_units, list) or not flat_units:
                raise ValueError("step1 拆分内容结构异常：units 必须是非空的 unit 对象数组")

            violations = _collect_reference_flat_violations(
                flat_units,
                project,
                episode=episode,
                novel_text=novel_text,
                caps=split_caps,
                source_language=project.get("source_language"),
            )
            if violations:
                # 违约不丢弃、也不写正式文件：产物连同报告落隔离草稿，由 agent 修复后经
                # 晋升工具重判。源文路径进 meta——重判时按整个 source/ 重解析会让原文锚的
                # 子串判定比产出时更松，一份改写过的锚可能在别集原文里恰好命中。
                # 基线读取与草稿写入在同一写临界区内完成：锁外各做一次的话，记下的基线可能
                # 描述的是另一份并发写入前的内容。
                with script_review.step1_write_lock(project_path, episode) as step1_path:
                    report = quarantine_and_report(
                        project_path,
                        episode,
                        QUARANTINE_KIND_STEP1,
                        content={"units": flat_units},
                        violations=violations,
                        # source 键一律写出（未指定源文时为 null）：晋升侧据此区分「原本就按整个
                        # source/ 产出」与「meta 被改坏」，缺键时不能默默退回更松的全目录解析。
                        # base_fingerprint 记下此刻正式文件的指纹（不存在时为 null）：这份草稿
                        # 修好晋升时若正式文件已被别的写入方改过，按基线比对中止并报冲突。
                        meta={
                            "source": source or None,
                            "base_fingerprint": script_review.content_fingerprint(step1_path),
                        },
                    )
                return {"content": [{"type": "text", "text": report}], "is_error": True}

            raw_units = _build_reference_units_from_flat(
                flat_units, project, episode=episode, max_refs=split_caps.max_refs
            )
            # 重拆分是刻意的整份重建，无基线可比对；写盘经单一出口。上一轮隔离草稿的清除与
            # 写盘同一临界区：正式文件已是这一份产物，旧草稿留着只会让 gate 与生成侧继续阻塞
            # 在一份已被取代的违约产物上。
            with script_review.step1_write_lock(project_path, episode) as step1_path:
                script_review.write_step1_locked(project_path, episode, {"units": raw_units})
                clear_quarantine(project_path, episode, QUARANTINE_KIND_STEP1)
            warning_lines = _reference_voice_warning_lines([f["text"] for f in flat_units], project, split_caps.voice)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": _reference_result_text(step1_path, raw_units, warning_lines, action="拆分"),
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("split_reference_video_units", exc)

    return _handler


# ---------------------------------------------------------------------------
# validate_and_promote_reference_draft
# ---------------------------------------------------------------------------


def validate_and_promote_reference_draft_tool(ctx: ToolContext):
    @tool(
        PROMOTE_TOOL_NAME,
        "重新全量校验本集的参考生视频隔离草稿（step1 拆分或 step2 视觉展开的违约产物），"
        "通过则晋升为正式文件并清除草稿，不通过则返回刷新后的违约报告。"
        "在按上一份报告修改过隔离草稿的 content 之后调用；可反复调用，无轮次上限。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "description": "剧集编号"}},
            "required": ["episode"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            project_path = ctx.project_path
            project_data = ctx.pm.load_project(ctx.project_name)

            # 分镜路线项目上残留的草稿不再晋升：晋升会按参考路径的形状覆盖 scripts/episode_N.json，
            # 而该项目走的是分镜路径。与 generate_episode_script 忽略这些残留同一判据。
            if not _uses_reference_video_units(project_data):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"❌ 第 {episode} 集当前不走参考生视频路径，"
                                "盘上的参考路径隔离草稿已与该集无关，不作晋升"
                            ),
                        }
                    ],
                    "is_error": True,
                }

            # step1 优先：step2 的保结构 diff 以正式 step1 为基底，step1 还在隔离态时判 step2
            # 只会拿旧基底得出误导性的结论。
            step1_draft = read_quarantine(project_path, episode, QUARANTINE_KIND_STEP1)
            if step1_draft is not None:
                return await _promote_reference_step1(ctx, episode, step1_draft)
            if quarantine_exists(project_path, episode, QUARANTINE_KIND_STEP1):
                raise ValueError(
                    f"step1 隔离草稿 {quarantine_path(project_path, episode, QUARANTINE_KIND_STEP1)} 不是合法的 JSON "
                    "信封（顶层须为对象且含 content 对象）；请修正该文件的 JSON 结构后重试"
                )

            if quarantine_exists(project_path, episode, QUARANTINE_KIND_STEP2):
                # 晋升同样受 step1 审核 gate 约束：隔离期间用户在 Web 端改过 step1 会让确认指纹
                # 失效、该集回到 pending_review，此时晋升等于拿一份用户没确认过的 step1 合成正式
                # 剧本——常规生成路径在工具入口就被 gate 拦下，两条路不该在这一位上分叉。
                if script_review.gate_blocks_step2(project_path, project_data, episode):
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "⏸️ 本集 step1 尚未经 web 审核确认（或确认后内容又被改），step2 草稿暂不晋升。"
                                    "请在 Web 端审阅并确认本集 step1 内容后再调用本工具。"
                                ),
                            }
                        ],
                        "is_error": True,
                    }
                # 用异步工厂而非裸构造：晋升同样经 _add_metadata 落盘，裸构造会把
                # metadata.generator 记成 "unknown"，与直接生成路径的同一份产物对不上。
                generator = await ScriptGenerator.create(project_path)
                result_path = await generator.promote_reference_step2_draft(episode)
                return {"content": [{"type": "text", "text": f"✅ step2 视觉展开已校验通过并晋升: {result_path}"}]}

            return {
                "content": [{"type": "text", "text": f"❌ 第 {episode} 集没有待处置的隔离草稿"}],
                "is_error": True,
            }
        except DraftViolation as exc:
            # 报告已由校验侧渲染（含逐条定位与处置指引），原样回传、不再加工具名前缀包裹。
            return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
        except Exception as exc:  # noqa: BLE001
            return tool_error(PROMOTE_TOOL_NAME, exc)

    return _handler


# ---------------------------------------------------------------------------
# split_narration_segments
# ---------------------------------------------------------------------------


def _validate_narration_segments(segments: list[dict], supported_durations: list[int]) -> None:
    """按 narration step1 读取契约做后校验：segment_id 唯一 + novel_text 非空白 + duration ∈ supported_durations。

    静态 ``NarrationStep1Segment.novel_text`` 的 ``min_length=1`` 只校验原始字符串长度，纯空白
    （如单个空格）能满足该约束却不携带任何实际旁白内容；此类片段在覆盖校验中经
    ``_normalize_for_coverage`` 折叠为空字符串后不消耗任何字符，不会被覆盖校验拦截，会被当作
    合法片段（携带真实 duration_seconds / 资产引用）写盘，产生"有时长但无旁白"的哑片段。

    静态 ``NarrationStep1Segment.duration_seconds`` 是 ``ge=1, le=60`` 开区间（复用既有片段 schema，
    不在 schema 层枚举硬约束），故超出 ``supported_durations`` 的时长能过 schema 校验；此处 fail-loud
    补上成员校验，与 ``ScriptGenerator._load_narration_step1`` 同口径——只有经此校验的内容才写盘成为
    step1 真值源，杜绝把非法时长拖到 step2 / 最终 save_script 才暴露。
    """
    ids = [s.get("segment_id") for s in segments]
    dupes = sorted(str(sid) for sid, count in Counter(ids).items() if count > 1)
    if dupes:
        raise ValueError(f"step1 拆分内容 segment_id 重复: {dupes}")
    blank = sorted(str(s.get("segment_id")) for s in segments if not str(s.get("novel_text") or "").strip())
    if blank:
        raise ValueError(f"step1 拆分内容 novel_text 为空白: {blank}")
    allowed = {int(d) for d in supported_durations}
    bad = sorted({s["duration_seconds"] for s in segments if int(s.get("duration_seconds") or 0) not in allowed})
    if bad:
        raise ValueError(f"step1 拆分内容 duration_seconds 非法（不在 {sorted(allowed)} 内）: {bad}")


def _validate_narration_asset_references(
    segments: list[dict],
    characters: dict[str, Any],
    scenes: dict[str, Any],
    props: dict[str, Any],
) -> None:
    """校验片段登记的角色 / 场景 / 道具已在 project.json 对应表注册，与 rv 侧
    ``_build_reference_units_from_flat`` 对资产引用的校验同口径：只信登记过的资产名，
    不允许模型发明或拼错的名称被当真值写盘、被 step2 视觉层只读消费。
    """
    missing: dict[str, list[str]] = {}
    for segment in segments:
        sid = str(segment.get("segment_id"))
        for field, bucket in (
            ("characters_in_segment", characters),
            ("scenes", scenes),
            ("props", props),
        ):
            names = segment.get(field) or []
            bad = sorted({str(name) for name in names if name not in bucket})
            if bad:
                missing[f"{sid}.{field}"] = bad
    if missing:
        raise ValueError(f"step1 拆分内容引用了未登记的资产名: {missing}；资产名必须逐字取自 project.json 三张表")


def _normalize_for_coverage(text: str) -> str:
    """把连续空白折叠为单个空格，不删除空白本身，仅消除空白的类型 / 数量差异。"""
    return re.sub(r"\s+", " ", text).strip()


def _validate_narration_novel_text_coverage(segments: list[dict], novel_text: str) -> None:
    """机械校验片段 ``novel_text`` 按序、完整覆盖源文，杜绝模型删减 / 改写 / 重排后仍被当真值落盘。

    片段边界处的空白存在与否天然歧义——模型选择的切分点可能落在源文空格上（该空格被
    切分本身"消耗"，不落在任一片段自身文本里），也可能落在无空格的 CJK / 标点邻接处，
    两者从拼接后的字符串本身无法可靠区分。因此仅在片段交界处允许可选的单个空格；片段
    自身文本内部与源文其余部分一律要求折叠后逐字相等，不能让边界宽容掩盖片段内部真实的
    删减、改写或词间空格丢失（后者是 CJK / 半角标点邻接边界的普适宽容规则曾误伤的场景）。
    """
    parts = [re.escape(_normalize_for_coverage(str(s.get("novel_text") or ""))) for s in segments]
    pattern = " ?".join(parts)
    if re.fullmatch(pattern, _normalize_for_coverage(novel_text)) is None:
        raise ValueError("step1 拆分内容 novel_text 未逐字、完整覆盖小说原文（存在删减、改写或重排）")


def split_narration_segments_tool(ctx: ToolContext):
    @tool(
        "split_narration_segments",
        "把本集小说原文按朗读节奏拆分为说书片段表（逐字 novel_text + 时长 + segment_break + 出场资产），"
        "保存到 drafts/episode_N/step1_segments.json，供 generate_episode_script（narration 模式）消费。"
        "novel_text 逐字保留原文、由 step2 透传，不经 step2 的 LLM 重出。dry_run=true 时仅返回 prompt。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {
                    "type": "string",
                    "description": "指定小说源文件路径（相对项目目录）；默认读取 source/ 下所有文本",
                },
                "instructions": _INSTRUCTIONS_SCHEMA,
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
            instructions, param_err = read_instructions_arg(args)
            if param_err is not None:
                return param_err

            project_path = ctx.project_path
            project = ctx.pm.load_project(ctx.project_name)

            try:
                novel_text = _load_novel_source(project_path, source)
            except ValueError as exc:
                return {"content": [{"type": "text", "text": f"❌ {exc}"}], "is_error": True}

            characters = project.get("characters")
            characters = characters if isinstance(characters, dict) else {}
            scenes = project.get("scenes")
            scenes = scenes if isinstance(scenes, dict) else {}
            props = project.get("props")
            props = props if isinstance(props, dict) else {}

            # narration 仅需 (default_duration, supported_durations)：无 unit 总时长 / references 概念，
            # 复用与 drama normalize 同口径的 best-effort 能力查询（resolver 故障软回退 [4,6,8]）。
            default_duration, supported_durations = await _fetch_caps_with_fallback(project, episode)
            prompt = build_narration_split_prompt(
                novel_text=novel_text,
                project_overview=project.get("overview", {}),
                characters=characters,
                scenes=scenes,
                props=props,
                default_duration=default_duration,
                supported_durations=supported_durations,
                episode=episode,
                # 输出语言取项目 source_language（生成内容语言的唯一真相源），与 normalize / reference 同口径。
                target_language=project.get("source_language") or "中文",
            )
            prompt = append_user_instructions(prompt, instructions)

            if dry_run:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"DRY RUN — 以下是将发送给文本模型的 Prompt:\n\n{prompt}\n\nPrompt 长度: {len(prompt)} 字符",
                        }
                    ]
                }

            # 结构化输出：response_schema 复用既有 NarrationStep1Draft（片段 schema）；
            # 本地解析复用同一 schema 保持同口径校验。片段时长的成员约束由下方 _validate_narration_segments 兜底。
            generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name=ctx.project_name)
            result = await generator.generate(
                TextGenerationRequest(
                    prompt=prompt,
                    response_schema=NarrationStep1Draft,
                    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                ),
                project_name=ctx.project_name,
            )
            content = _parse_step1_json(
                result.text, NarrationStep1Draft, label="step1 拆分内容", top_shape="{segments}"
            )

            raw_segments = content.get("segments")
            if not isinstance(raw_segments, list) or not raw_segments:
                raise ValueError("step1 拆分内容结构异常：segments 必须是非空的片段对象数组")
            _validate_narration_segments(raw_segments, supported_durations)
            _validate_narration_asset_references(raw_segments, characters, scenes, props)
            _validate_narration_novel_text_coverage(raw_segments, novel_text)

            drafts_dir = episode_drafts_dir(project_path, episode)
            drafts_dir.mkdir(parents=True, exist_ok=True)
            step1_path = drafts_dir / STEP1_FILENAMES["narration"]
            # step1 真相源须原子写入（同 normalize_drama_script）：避免中断 / 并发重跑留下半写 JSON。
            atomic_write_json(step1_path, content)

            total_chars = sum(len(str(s.get("novel_text") or "")) for s in raw_segments)
            total_seconds = sum(int(s.get("duration_seconds") or 0) for s in raw_segments)
            break_count = sum(1 for s in raw_segments if s.get("segment_break"))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"✅ 说书片段拆分（结构化 step1）已保存: {step1_path}\n"
                            f"📊 生成统计: {len(raw_segments)} 个片段 / {total_chars} 字，"
                            f"预计总时长 {total_seconds} 秒；segment_break 标记 {break_count} 个"
                        ),
                    }
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("split_narration_segments", exc)

    return _handler


__all__ = [
    "get_video_capabilities_tool",
    "generate_episode_script_tool",
    "confirm_script_review_tool",
    "normalize_drama_script_tool",
    "split_reference_video_units_tool",
    "validate_and_promote_reference_draft_tool",
    "split_narration_segments_tool",
]
