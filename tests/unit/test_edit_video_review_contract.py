"""edit-video 的审阅—交付—验收链路：skill 与审片子智能体里写死的机器契约须与代码真相源一致。

这些写法是 Agent 照抄的契约：链接格式由前端解析、子智能体名由访问策略按名约束、工具参数由请求模型校验，
任一处改了而 .md 没跟上，Agent 生成的链接点不开、子智能体失去只读约束，或提交被参数校验拒绝。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.media_tools.narration_audio import GenerateNarrationAudioRequest
from server.media_tools.videos import GenerateVideosRequest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "agent_runtime_profile/.claude/skills/edit-video/SKILL.md"
REVIEWER = REPO / "agent_runtime_profile/.claude/agents/review-footage.md"
APP_LINK = REPO / "frontend/src/utils/app-link.ts"

_LINK_TEMPLATE = re.compile(r"`(/app/projects/\{项目名\}/episodes/[^`]+)`")


def _section(text: str, heading: str) -> str:
    """取一个 ``##`` 节的正文，到下一个 ``##`` 为止。"""

    start = text.index(f"## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _frontmatter(path: Path) -> dict[str, object]:
    _, frontmatter, _ = path.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(frontmatter)


def test_review_footage_definition_grants_exactly_the_tools_the_access_policy_allows() -> None:
    """frontmatter 的 tools 由 CLI 收窄，访问策略在 hook 上再拒一次；两份名单不一致时总有一层失效。"""

    meta = _frontmatter(REVIEWER)
    tools = {tool.strip() for tool in str(meta["tools"]).split(",")}

    assert meta["name"] in AgentAccessPolicy.READ_ONLY_SUBAGENT_TOOLS
    assert tools == AgentAccessPolicy.READ_ONLY_SUBAGENT_TOOLS[str(meta["name"])]


def test_edit_video_dispatches_the_reviewer_by_its_defined_name() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    name = str(_frontmatter(REVIEWER)["name"])

    assert f"`{name}`" in _section(skill, "首轮全量审阅")
    assert f"`{name}`" in _section(skill, "执行勾选项")


def test_jump_links_in_the_delivery_use_the_formats_the_app_parses() -> None:
    """交付里的两种「跳到这里看」链接逐字取自前端解析器文件头的格式说明。"""

    delivery = _section(SKILL.read_text(encoding="utf-8"), "一轮结束的交付")
    app_formats = set(_LINK_TEMPLATE.findall(APP_LINK.read_text(encoding="utf-8")))
    skill_formats = set(_LINK_TEMPLATE.findall(delivery))

    assert len(app_formats) == 2
    assert skill_formats == app_formats


def test_delivery_carries_summary_links_and_a_checklist_with_every_required_field() -> None:
    delivery = _section(SKILL.read_text(encoding="utf-8"), "一轮结束的交付")

    for part in ("改动总结", "「跳到这里看」链接", "勾选清单", "AskUserQuestion", "`multiSelect: true`"):
        assert part in delivery, part
    regenerate = next(line for line in delivery.splitlines() if "**重新生成**" in line)
    for field in ("视频单元", "问题与时间点", "是否修改提示词", "预计费用", "时长档位", "「全部重新生成」"):
        assert field in regenerate, field
    rewrite = next(line for line in delivery.splitlines() if "**改写画外音**" in line)
    for field in ("原文", "改后文字", "预计缩短的秒数"):
        assert field in rewrite, field


def test_preview_and_submission_name_real_generate_videos_parameters() -> None:
    """预检与正式提交照抄的参数名都是请求模型的字段：写错一个，提交就被参数校验拒绝。"""

    skill = SKILL.read_text(encoding="utf-8")
    fields = set(GenerateVideosRequest.model_fields)
    preview = _section(skill, "一轮结束的交付")
    submit = _section(skill, "执行勾选项")

    assert {"preview", "target", "force"} <= fields
    assert "`preview: true`" in preview
    assert "`force: true`" in submit
    assert "confirmed_request_durations" in fields
    assert "`confirmed_request_durations`" in submit
    assert "segment_ids" in GenerateNarrationAudioRequest.model_fields
    assert "`segment_ids`" in submit
