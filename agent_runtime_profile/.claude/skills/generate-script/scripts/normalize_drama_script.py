#!/usr/bin/env python3
"""
normalize_drama_script.py - 使用当前配置的文本后端生成规范化剧本

将 source/ 小说原文转化为 Markdown 格式的规范化剧本（step1_normalized_script.md），
供 generate_script.py 消费。

用法:
    python normalize_drama_script.py --episode <N>
    python normalize_drama_script.py --episode <N> --source <file>
    python normalize_drama_script.py --episode <N> --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# parents[4] 对应 repo root，相对路径 agent_runtime_profile/.claude/skills/generate-script/scripts/
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.config.resolver import ConfigResolver  # noqa: E402
from lib.db import async_session_factory  # noqa: E402
from lib.project_manager import ProjectManager  # noqa: E402
from lib.text_backends.base import TextGenerationRequest, TextTaskType  # noqa: E402
from lib.text_backends.factory import create_text_backend_for_task  # noqa: E402

logger = logging.getLogger(__name__)

# caps 查询失败时的安全 fallback；避免给 LLM 错误的秒数锚点。
_FALLBACK_SUPPORTED_DURATIONS = [4, 6, 8]


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    scenes: dict,
    props: dict,
    default_duration: int | None,
    supported_durations: list[int],
) -> str:
    """构建规范化剧本的 Prompt

    Args:
        default_duration: 用户项目偏好（project.json.default_duration），None 表示未设置
        supported_durations: 当前视频模型允许的单场景时长取值集合
    """
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
    """查 ConfigResolver 拿 (default_duration, supported_durations)。

    查询失败（video_backend 未配置、model 找不到、DB 不可达等）时返回 fallback，
    以便 dry-run / 无 backend 项目仍能跑通，只是秒数规则退化为通用值。
    """
    resolver = ConfigResolver(async_session_factory)
    try:
        caps = await resolver.video_capabilities(project_name)
    except (FileNotFoundError, ValueError) as exc:
        logger.info("video_capabilities 不可解析，使用 fallback [4,6,8]：%s", exc)
        return None, list(_FALLBACK_SUPPORTED_DURATIONS)
    except Exception as exc:  # noqa: BLE001 — DB/网络异常也降级，不阻塞 dry-run
        logger.warning("video_capabilities 查询异常，使用 fallback [4,6,8]：%s", exc)
        return None, list(_FALLBACK_SUPPORTED_DURATIONS)

    durations = list(caps.get("supported_durations") or []) or list(_FALLBACK_SUPPORTED_DURATIONS)
    default = caps.get("default_duration")
    default_int = int(default) if isinstance(default, int) else None
    return default_int, durations


async def amain() -> None:
    parser = argparse.ArgumentParser(
        description="使用当前配置的文本后端生成规范化剧本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    %(prog)s --episode 1
    %(prog)s --episode 1 --source source/chapter1.txt
    %(prog)s --episode 1 --dry-run
        """,
    )

    parser.add_argument("--episode", "-e", type=int, required=True, help="剧集编号")
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        default=None,
        help="指定小说源文件路径（默认读取 source/ 目录下所有文件）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅显示 Prompt，不实际调用 API")

    args = parser.parse_args()

    pm, project_name = ProjectManager.from_cwd()
    project_path = pm.get_project_path(project_name)
    project = pm.load_project(project_name)

    if args.source:
        source_path = (project_path / args.source).resolve()
        if not source_path.is_relative_to(project_path.resolve()):
            print(f"❌ 路径超出项目目录: {source_path}")
            sys.exit(1)
        if not source_path.exists():
            print(f"❌ 未找到源文件: {source_path}")
            sys.exit(1)
        novel_text = source_path.read_text(encoding="utf-8")
    else:
        source_dir = project_path / "source"
        if not source_dir.exists() or not any(source_dir.iterdir()):
            print(f"❌ source/ 目录为空或不存在: {source_dir}")
            sys.exit(1)
        texts = []
        for f in sorted(source_dir.iterdir()):
            if f.suffix in (".txt", ".md", ".text"):
                texts.append(f.read_text(encoding="utf-8"))
        novel_text = "\n\n".join(texts)

    if not novel_text.strip():
        print("❌ 小说原文为空")
        sys.exit(1)

    default_duration, supported_durations = await _fetch_video_caps(project_name)

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

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN - 以下是将发送给当前文本后端的 Prompt:")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print(f"\nPrompt 长度: {len(prompt)} 字符")
        return

    backend = await create_text_backend_for_task(TextTaskType.SCRIPT)
    print(f"正在使用文本后端 {backend.name}/{backend.model} 生成规范化剧本...")
    result = await backend.generate(TextGenerationRequest(prompt=prompt, max_output_tokens=16000))
    response = result.text

    drafts_dir = project_path / "drafts" / f"episode_{args.episode}"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    step1_path = drafts_dir / "step1_normalized_script.md"
    step1_path.write_text(response.strip(), encoding="utf-8")
    print(f"✅ 规范化剧本已保存: {step1_path}")

    lines = [
        line
        for line in response.split("\n")
        if line.strip().startswith("|") and "场景 ID" not in line and "---" not in line
    ]
    scene_count = len(lines)
    print(f"\n📊 生成统计: {scene_count} 个场景")


if __name__ == "__main__":
    asyncio.run(amain())
