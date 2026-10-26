"""edit-video 的审阅—交付—验收链路：skill 与审片子智能体里写死的机器契约须与代码真相源一致。

这些写法是 Agent 照抄的契约：链接格式由前端解析、子智能体名由访问策略按名约束、工具参数由请求模型校验，
任一处改了而 .md 没跟上，Agent 生成的链接点不开、子智能体失去只读约束，或提交被参数校验拒绝。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.session_manager import SessionManager
from server.media_tools.narration_audio import GenerateNarrationAudioRequest
from server.media_tools.videos import GenerateVideosRequest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "agent_runtime_profile/.claude/skills/edit-video/SKILL.md"
REVIEWER = REPO / "agent_runtime_profile/.claude/agents/review-footage.md"
APP_LINK = REPO / "frontend/src/utils/app-link.ts"

_LINK_TEMPLATE = re.compile(r"`(/app/projects/\{项目名\}/episodes/[^`]+)`")
#: 勾选清单的类目是缩进列表项开头的加粗名，交付与执行两节用同一组名字对上。
_CHECKLIST_CATEGORY = re.compile(r"^\s*- \*\*([^*]+)\*\*", re.MULTILINE)


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


def test_checklist_is_asked_through_a_registered_multi_select_question() -> None:
    """勾选清单经 AskUserQuestion 多选一次问完：工具名与参数都是 SDK 契约，写错 Agent 就问不出清单。"""

    delivery = _section(SKILL.read_text(encoding="utf-8"), "一轮结束的交付")

    assert "AskUserQuestion" in SessionManager.DEFAULT_ALLOWED_TOOLS
    assert "AskUserQuestion" in delivery
    assert "`multiSelect: true`" in delivery


def test_every_checklist_category_has_an_execution_rule() -> None:
    """清单里的每类待决定事项都要在「执行勾选项」里有对应的执行办法，反之亦然：
    多出一类就是创作者勾了却没人执行，少一类就是执行规则无从触发。只比对两处的加粗类目名，不看措辞。"""

    skill = SKILL.read_text(encoding="utf-8")
    offered = set(_CHECKLIST_CATEGORY.findall(_section(skill, "一轮结束的交付")))
    executed = set(_CHECKLIST_CATEGORY.findall(_section(skill, "执行勾选项")))

    assert offered, "交付一节没有勾选清单类目，断言失去意义"
    assert offered == executed


def test_paid_regeneration_option_quotes_the_previewed_cost() -> None:
    """重新生成是付费动作，选项须带预检报价让创作者勾选前知道花多少。
    这是费用防线，按 #3445 的豁免只断言该类目行同时点名预检工具与「费用」，不锁整句。"""

    delivery = _section(SKILL.read_text(encoding="utf-8"), "一轮结束的交付")
    paid = [line for line in delivery.splitlines() if _CHECKLIST_CATEGORY.match(line) and "generate_videos" in line]

    assert len(paid) == 1
    assert "`preview: true`" in paid[0]
    assert "费用" in paid[0]


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
