# 参考生视频模式 PR6：Agent 工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Claude Agent 在 `generation_mode=reference_video` 时能端到端自动跑完项目——从小说原文到生成视频。新增 `split-reference-video-units` subagent、扩展 `generate-script`/`generate-video` skill、改造 `manga-workflow` 按 mode 分支，并更新 `CLAUDE.md` 与参考文档。

**Architecture:** PR2 已落地 `ReferenceVideoScript`/`shot_parser`/`effective_mode()`；PR3 已落地 `/reference-videos` 路由 + `execute_reference_video_task` executor。PR6 只在**编排层和 skill 脚本**里加分支：`ScriptGenerator` 增加 `reference_video` 分支读 `step1_reference_units.md` 并用 `ReferenceVideoScript` schema；`generate_video.py` 检测脚本是 `video_units` 还是 `segments/scenes` 后分派到对应任务类型；`manga-workflow` 在 Step 3/4/7/8 按 `effective_mode` 选择 subagent 与参数。

**Tech Stack:** Python 3.11+ / Pydantic / pytest；Claude Agent SDK；`lib/generation_queue_client.py`；Markdown skill/subagent 文档。

---

## 参考设计

- Spec: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md` §7（Agent 工作流）
- Roadmap: `docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md` §PR6
- PR2 deliverables：
  - `lib/script_models.py:160-213` — `Shot` / `ReferenceResource` / `ReferenceVideoUnit` / `ReferenceVideoScript`
  - `lib/reference_video/shot_parser.py` — `parse_prompt()` / `render_prompt_for_backend()` / `resolve_references()` / `compute_duration_from_shots()`
  - `lib/project_manager.py:35-46` — `effective_mode(project, episode)`
- PR3 deliverables：
  - `server/routers/reference_videos.py` — 6 个端点
  - `server/services/reference_video_tasks.py` — `execute_reference_video_task`（`task_type="reference_video"` / `media_type="video"`）

---

## 文件结构总览

### 新增

```
agent_runtime_profile/.claude/agents/split-reference-video-units.md        # 新 subagent
agent_runtime_profile/.claude/references/generation-modes.md               # 三种生成模式完整路径文档
lib/prompt_builders_reference.py                                           # reference_video prompt builder（从 prompt_builders_script 拆出）
tests/lib/test_prompt_builders_reference.py                                # prompt 构建器单测
tests/lib/test_script_generator_reference_branch.py                        # ScriptGenerator reference 分支单测
tests/scripts/test_generate_video_reference_branch.py                      # generate_video.py reference 分派单测
```

### 改造

```
lib/script_generator.py                                                    # 加 reference_video 分支
agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py     # effective_mode + step1 文件选择
agent_runtime_profile/.claude/skills/generate-script/SKILL.md              # 前置条件加第三类中间文件
agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py       # 检测 video_units 分派
agent_runtime_profile/.claude/skills/generate-video/SKILL.md               # 补 reference 模式说明
agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md               # Step 3/4/6/7 按 mode 分支
agent_runtime_profile/CLAUDE.md                                            # generation_mode 概念、目录、技能表
```

### 删除

```
agent_runtime_profile/.claude/references/content-modes.md                  # 被 generation-modes.md 取代
```

---

## Task 1：新增 `build_reference_video_prompt()` prompt 构建器

**Files:**
- Create: `lib/prompt_builders_reference.py`
- Test: `tests/lib/test_prompt_builders_reference.py`

- [ ] **Step 1.1: 写失败测试**

写 `tests/lib/test_prompt_builders_reference.py`：

```python
"""reference_video prompt builder 单元测试。

Spec §7.3、§4.2/4.3。
"""

from lib.prompt_builders_reference import build_reference_video_prompt


def test_build_reference_video_prompt_contains_required_sections():
    project_overview = {
        "synopsis": "少年入江湖",
        "genre": "武侠",
        "theme": "成长",
        "world_setting": "北宋江湖",
    }
    characters = {"主角": {"description": "少年剑客"}, "张三": {"description": "酒客"}}
    scenes = {"酒馆": {"description": "黑木桌椅的江湖酒馆"}}
    props = {"长剑": {"description": "祖传青锋"}}
    step1_md = "| unit | 时长 | shots | references |\n| E1U1 | 8s | 2 | 主角,酒馆 |"

    prompt = build_reference_video_prompt(
        project_overview=project_overview,
        style="国漫",
        style_description="水墨渲染风格",
        characters=characters,
        scenes=scenes,
        props=props,
        units_md=step1_md,
        supported_durations=[5, 8, 10],
        max_refs=9,
        aspect_ratio="9:16",
    )

    # 必备上下文
    assert "北宋江湖" in prompt
    assert "水墨渲染风格" in prompt
    # 三类资产名称都必须出现（MentionPicker 候选源）
    assert "主角" in prompt and "张三" in prompt
    assert "酒馆" in prompt
    assert "长剑" in prompt
    # step1 内容必须透传
    assert "E1U1" in prompt
    # 关键 prompt 指令
    assert "@名称" in prompt
    assert "Shot" in prompt
    # schema 约束
    assert "video_units" in prompt
    assert "references" in prompt
    # 时长约束
    assert "5" in prompt or "8" in prompt
    assert "9" in prompt  # max_refs


def test_build_reference_video_prompt_emphasizes_no_appearance_description():
    """spec §7.3 规则 3：描述里用 @名称，不描述外貌。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="style",
        style_description="desc",
        characters={"A": {"description": "d"}},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[8],
        max_refs=9,
    )
    assert "外貌" in prompt  # 有反向说明


def test_build_reference_video_prompt_lists_shot_max_count():
    """spec §4.2：每 unit 1-4 shot。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[8],
        max_refs=9,
    )
    assert "4" in prompt  # shot 数量上限
```

- [ ] **Step 1.2: 运行测试确认失败**

运行：`uv run pytest tests/lib/test_prompt_builders_reference.py -v`
预期：FAIL（`lib.prompt_builders_reference` 不存在）

- [ ] **Step 1.3: 写 `lib/prompt_builders_reference.py`**

```python
"""参考生视频模式 Prompt 构建器。

Spec §7.3 的 LLM prompt 模板。
"""

from __future__ import annotations


def _format_asset_names(assets: dict | None) -> str:
    if not assets:
        return "（无）"
    return "\n".join(f"- {name}: {meta.get('description', '') if isinstance(meta, dict) else ''}" for name, meta in assets.items())


def build_reference_video_prompt(
    *,
    project_overview: dict,
    style: str,
    style_description: str,
    characters: dict,
    scenes: dict,
    props: dict,
    units_md: str,
    supported_durations: list[int],
    max_refs: int,
    aspect_ratio: str = "9:16",
    target_language: str = "中文",
) -> str:
    """构建参考生视频模式的 LLM Prompt。

    Args:
        project_overview: 项目概述（synopsis, genre, theme, world_setting）。
        style / style_description: 视觉风格标签与描述。
        characters / scenes / props: 三类已注册资产字典（用于候选列表）。
        units_md: `step1_reference_units.md` 内容（subagent 输出）。
        supported_durations: 当前视频模型支持的单镜头时长列表（秒）。
        max_refs: 当前视频模型支持的最大参考图数。
    """
    character_names = list(characters.keys())
    scene_names = list(scenes.keys())
    prop_names = list(props.keys())

    durations_desc = "/".join(str(d) for d in supported_durations) + "s"

    return f"""你的任务是为短视频生成「参考生视频」模式的 JSON 剧本。请仔细遵循以下指示：

**重要：所有输出内容必须使用{target_language}。仅 JSON 键名和枚举值使用英文。**

1. 你将获得故事概述、视觉风格、已注册的角色/场景/道具列表，以及 Step 1 已拆分好的 video_units 表。

2. 为每个 video_unit 生成 `ReferenceVideoScript.video_units[]` 数组项，并遵循如下约束：

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
风格：{style}
描述：{style_description}
画面比例：{aspect_ratio}
</style>

<characters>
{_format_asset_names(characters)}
</characters>

<scenes>
{_format_asset_names(scenes)}
</scenes>

<props>
{_format_asset_names(props)}
</props>

<step1_units>
{units_md}
</step1_units>

3. 每个 unit 的生成规则：

a. **unit_id**：保留 Step 1 中的 `E{{集数}}U{{序号}}`。

b. **shots**：1-4 个 shot。每个 shot 含：
   - `duration`：整数秒，取值必须在当前模型支持列表中：{durations_desc}
   - `text`：中文镜头描述，聚焦当下瞬间可见画面，**仅**用 `@名称` 引用角色/场景/道具——**不要**写外貌、服装、场景细节（这些由参考图提供）。
   - 每 unit 所有 shot `duration` 之和即该 unit `duration_seconds`。

c. **references**：`{{type, name}}` 列表，顺序决定 `[图N]` 编号。
   - `type` 取值 character / scene / prop。
   - `name` 必须来自以下候选，否则会校验失败：
     - character: {", ".join(character_names) or "（无）"}
     - scene: {", ".join(scene_names) or "（无）"}
     - prop: {", ".join(prop_names) or "（无）"}
   - 每个 shot `text` 中出现的 `@名称` 都必须在 references 里注册一次。
   - **references 数量不得超过 {max_refs}**（模型上限），超出时把次要角色合并到背景描述。

d. **duration_seconds**：所有 shot `duration` 之和；不要手动覆盖。

e. **transition_to_next**：默认 "cut"，如明显切换时间/空间可用 "fade" / "dissolve"。

f. **note**：可选，人类备注；通常留空。

4. 整集 `ReferenceVideoScript` 顶层字段：
   - `episode`、`title`、`summary`、`novel.title` / `novel.chapter` 必填。
   - `content_mode` 固定 "reference_video"。
   - `duration_seconds` 可先写 0，由 caller 重算。

5. 关键约束复核：
   - 每 unit 最多 **4 个 shot**；所有 shot 时长之和应贴近 Step 1 预估。
   - `@名称` 只能引用在 characters / scenes / props 三张表中已注册的名字。
   - 禁止在 shot `text` 中描写角色外貌、服装、场景细节（参考图负责视觉一致性）。
   - 禁止发明新的资产名称。

请根据 <step1_units> 逐 unit 产出。
"""
```

- [ ] **Step 1.4: 运行测试确认通过**

运行：`uv run pytest tests/lib/test_prompt_builders_reference.py -v`
预期：3 个测试全部 PASS。

- [ ] **Step 1.5: ruff 检查**

```bash
uv run ruff check lib/prompt_builders_reference.py tests/lib/test_prompt_builders_reference.py
uv run ruff format lib/prompt_builders_reference.py tests/lib/test_prompt_builders_reference.py
```

- [ ] **Step 1.6: 提交**

```bash
git add lib/prompt_builders_reference.py tests/lib/test_prompt_builders_reference.py
git commit -m "feat(lib): add reference_video prompt builder (PR6 agent workflow)"
```

---

## Task 2：扩展 `ScriptGenerator` 支持 reference_video 分支

**Files:**
- Modify: `lib/script_generator.py`
- Test: `tests/lib/test_script_generator_reference_branch.py`

- [ ] **Step 2.1: 写失败测试**

新建 `tests/lib/test_script_generator_reference_branch.py`：

```python
"""ScriptGenerator reference_video 分支测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.script_generator import ScriptGenerator


@pytest.fixture
def reference_project(tmp_path: Path) -> Path:
    """造一个 reference_video 模式的最小项目。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        """{
          "title": "t",
          "content_mode": "narration",
          "generation_mode": "reference_video",
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫",
          "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}},
          "props": {},
          "episodes": [{"episode": 1, "title": "t1", "generation_mode": "reference_video"}]
        }""",
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.md").write_text(
        "| unit | shots | refs |\n| E1U1 | Shot1(4s) | 主角,酒馆 |\n",
        encoding="utf-8",
    )
    return project_dir


def test_script_generator_build_prompt_selects_reference_branch(reference_project: Path):
    """当 effective_mode == reference_video 时，build_prompt 必须走 reference 分支。"""
    gen = ScriptGenerator(reference_project)
    prompt = gen.build_prompt(episode=1)
    # reference 分支特征标签
    assert "video_units" in prompt
    assert "@名称" in prompt
    # 不应出现 narration / drama 特征
    assert "characters_in_segment" not in prompt


def test_script_generator_reads_step1_reference_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = gen.build_prompt(episode=1)
    # step1_reference_units.md 的内容必须透传
    assert "E1U1" in prompt


@pytest.mark.asyncio
async def test_script_generator_uses_reference_schema_on_generate(reference_project: Path, monkeypatch):
    """_parse_response 在 reference 模式下用 ReferenceVideoScript 校验。"""
    from lib.script_models import ReferenceVideoScript

    fake_generator = MagicMock()
    fake_generator.model = "mock"
    fake_generator.generate = AsyncMock(
        return_value=MagicMock(
            text=(
                '{"episode":1,"title":"t","content_mode":"reference_video",'
                '"summary":"s","novel":{"title":"t","chapter":"1"},'
                '"video_units":[{"unit_id":"E1U1",'
                '"shots":[{"duration":4,"text":"@主角 推门"}],'
                '"references":[{"type":"character","name":"主角"}],'
                '"duration_seconds":4,"duration_override":false,"transition_to_next":"cut"}]}'
            )
        )
    )

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    out = await gen.generate(episode=1)
    assert out.exists()
    import json as _j

    data = _j.loads(out.read_text(encoding="utf-8"))
    assert data["content_mode"] == "reference_video"
    assert len(data["video_units"]) == 1

    # 确认生成时用了 ReferenceVideoScript schema
    call_kwargs = fake_generator.generate.await_args.args[0]
    assert call_kwargs.response_schema is ReferenceVideoScript
```

- [ ] **Step 2.2: 运行测试确认失败**

运行：`uv run pytest tests/lib/test_script_generator_reference_branch.py -v`
预期：FAIL — `ScriptGenerator` 尚未识别 reference 模式。

- [ ] **Step 2.3: 改造 `lib/script_generator.py`**

读当前 `lib/script_generator.py` 看完整结构。关键修改点：

① 新 import（在文件顶部已有 `DramaEpisodeScript, NarrationEpisodeScript` 附近）：

```python
from lib.script_models import (
    DramaEpisodeScript,
    NarrationEpisodeScript,
    ReferenceVideoScript,
)
from lib.project_manager import effective_mode
from lib.prompt_builders_reference import build_reference_video_prompt
```

② 在 `__init__` 中计算 `self.effective_mode`：

```python
def __init__(self, project_path: str | Path, generator: TextGenerator | None = None) -> None:
    self.project_path = Path(project_path)
    self.generator = generator
    self.project_json = self._load_project_json()
    self.content_mode = self.project_json.get("content_mode", "narration")
    # Spec §4.6：effective_mode 优先 episode → project → 默认 storyboard
    project_gen_mode = self.project_json.get("generation_mode")
    self.generation_mode = project_gen_mode if project_gen_mode in {"storyboard", "grid", "reference_video"} else "storyboard"
```

> 说明：`ScriptGenerator` 只在项目级判断 `generation_mode` 是否是 reference；集级差异由 CLI 脚本层处理（Task 4）。

③ 改造 `generate()` 的调度：在现有 narration/drama 分支前加 reference 分支：

```python
if self.generation_mode == "reference_video":
    prompt = build_reference_video_prompt(
        project_overview=self.project_json.get("overview", {}),
        style=self.project_json.get("style", ""),
        style_description=self.project_json.get("style_description", ""),
        characters=self.project_json.get("characters", {}),
        scenes=self.project_json.get("scenes", {}),
        props=self.project_json.get("props", {}),
        units_md=step1_md,
        supported_durations=self._resolve_supported_durations() or [4, 6, 8],
        max_refs=self._resolve_max_refs(),
        aspect_ratio=self._resolve_aspect_ratio(),
    )
    schema = ReferenceVideoScript
elif self.content_mode == "narration":
    # ...existing...
else:
    # ...existing...
```

④ 同步改造 `build_prompt()`（dry-run 路径）：加相同 reference 分支。

⑤ 改造 `_load_step1()`：当 reference 模式时读 `step1_reference_units.md`；原 narration/drama 逻辑保留。

```python
def _load_step1(self, episode: int) -> str:
    drafts_path = self.project_path / "drafts" / f"episode_{episode}"
    if self.generation_mode == "reference_video":
        primary_path = drafts_path / "step1_reference_units.md"
        fallback_path = None
    elif self.content_mode == "narration":
        primary_path = drafts_path / "step1_segments.md"
        fallback_path = drafts_path / "step1_normalized_script.md"
    else:
        primary_path = drafts_path / "step1_normalized_script.md"
        fallback_path = drafts_path / "step1_segments.md"

    if not primary_path.exists():
        if fallback_path is not None and fallback_path.exists():
            logger.warning("未找到 Step 1 文件: %s，改用 %s", primary_path, fallback_path)
            primary_path = fallback_path
        else:
            raise FileNotFoundError(f"未找到 Step 1 文件: {primary_path}")
    with open(primary_path, encoding="utf-8") as f:
        return f.read()
```

⑥ 改造 `_parse_response()`：reference 分支用 `ReferenceVideoScript`：

```python
try:
    if self.generation_mode == "reference_video":
        validated = ReferenceVideoScript.model_validate(data)
    elif self.content_mode == "narration":
        validated = NarrationEpisodeScript.model_validate(data)
    else:
        validated = DramaEpisodeScript.model_validate(data)
    return validated.model_dump()
except ValidationError as e:
    logger.warning("数据验证警告: %s", e)
    return data
```

⑦ 改造 `_add_metadata()`：reference 模式要算 `duration_seconds` 为 `sum(unit.duration_seconds)`，并在 `content_mode` 被设为 "reference_video"：

```python
def _add_metadata(self, script_data: dict, episode: int) -> dict:
    script_data.setdefault("episode", episode)
    if self.generation_mode == "reference_video":
        script_data["content_mode"] = "reference_video"
    else:
        script_data.setdefault("content_mode", self.content_mode)

    # ...novel / timestamp 保持不变...

    if self.generation_mode == "reference_video":
        units = script_data.get("video_units", [])
        script_data["metadata"]["total_units"] = len(units)
        script_data["duration_seconds"] = sum(int(u.get("duration_seconds", 0)) for u in units)
    elif self.content_mode == "narration":
        # ...existing segments...
    else:
        # ...existing scenes...

    script_data.pop("characters_in_episode", None)
    script_data.pop("clues_in_episode", None)
    return script_data
```

⑧ 新方法 `_resolve_max_refs()`：

```python
def _resolve_max_refs(self) -> int:
    """从 video_backend registry 解析最大参考图数。缺省返回 9。"""
    video_backend = self.project_json.get("video_backend")
    if video_backend and isinstance(video_backend, str) and "/" in video_backend:
        provider_id, model_id = video_backend.split("/", 1)
        provider_meta = PROVIDER_REGISTRY.get(provider_id)
        if provider_meta:
            model_info = provider_meta.models.get(model_id)
            # model_info 可能暴露 max_reference_images；若未暴露则按 provider 默认
            max_refs = getattr(model_info, "max_reference_images", None) if model_info else None
            if isinstance(max_refs, int) and max_refs > 0:
                return max_refs
    # 按 provider 粗粒度兜底（与 lib/.../reference_video_tasks.py _PROVIDER_LIMITS 对齐）
    provider_id = (video_backend or "").split("/", 1)[0].lower() if video_backend else ""
    return {"gemini": 3, "openai": 1, "grok": 7, "ark": 9}.get(provider_id, 9)
```

- [ ] **Step 2.4: 运行测试确认通过**

```bash
uv run pytest tests/lib/test_script_generator_reference_branch.py -v
# 同时确认旧测试没退化
uv run pytest tests/lib/test_script_generator.py -v  # 若存在
```

预期：reference 分支 3 个新测试 PASS；旧测试不回归。

- [ ] **Step 2.5: ruff 检查**

```bash
uv run ruff check lib/script_generator.py tests/lib/test_script_generator_reference_branch.py
uv run ruff format lib/script_generator.py tests/lib/test_script_generator_reference_branch.py
```

- [ ] **Step 2.6: 提交**

```bash
git add lib/script_generator.py tests/lib/test_script_generator_reference_branch.py
git commit -m "feat(lib): ScriptGenerator supports reference_video mode (PR6)"
```

---

## Task 3：CLI `generate_script.py` 按 effective_mode 选 step1 文件

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py`

- [ ] **Step 3.1: 阅读当前脚本**

`generate_script.py` 目前只按 `content_mode` 选 step1 文件，需要额外识别 `effective_mode == reference_video`。

- [ ] **Step 3.2: 修改脚本**

将 Step 1 文件名识别逻辑改写成：

```python
# 识别 generation_mode（项目 + 集级）
project_json_path = project_path / "project.json"
content_mode = "narration"
generation_mode = "storyboard"
project_data: dict = {}
if project_json_path.exists():
    try:
        project_data = _json.loads(project_json_path.read_text(encoding="utf-8"))
        content_mode = project_data.get("content_mode", "narration")
        generation_mode = project_data.get("generation_mode", "storyboard")
    except Exception:
        pass
# 集级覆盖（Spec §4.6）
for ep in project_data.get("episodes") or []:
    if ep.get("episode") == args.episode and ep.get("generation_mode"):
        generation_mode = ep["generation_mode"]
        break

drafts_path = project_path / "drafts" / f"episode_{args.episode}"
if generation_mode == "reference_video":
    step1_path = drafts_path / "step1_reference_units.md"
    step1_hint = "split-reference-video-units subagent（Step 1）"
elif content_mode == "drama":
    step1_path = drafts_path / "step1_normalized_script.md"
    step1_hint = "normalize_drama_script.py"
else:
    step1_path = drafts_path / "step1_segments.md"
    step1_hint = "片段拆分（Step 1）"
```

> 注意：`ScriptGenerator.__init__` 已经自己处理 `generation_mode`，CLI 层只做前置存在性检查。

- [ ] **Step 3.3: 手动 dry-run 验证**

在某个 reference_video 项目 fixture 里运行：

```bash
python agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py --episode 1 --dry-run
```

预期：打印的 prompt 里含 `video_units` / `@名称`。

若没有 reference 模式 fixture，可用 `pytest` 驱动 `ScriptGenerator(...).build_prompt(1)` 替代（Task 2 已覆盖）。

- [ ] **Step 3.4: 回归其它模式**

对一个 narration 项目再次 dry-run：预期 prompt 里是 `segments` / `novel_text`。

- [ ] **Step 3.5: 提交**

```bash
git add agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py
git commit -m "feat(agent): generate_script CLI honors reference_video generation_mode"
```

---

## Task 4：新增 subagent `split-reference-video-units`

**Files:**
- Create: `agent_runtime_profile/.claude/agents/split-reference-video-units.md`

- [ ] **Step 4.1: 阅读既有 subagent 找风格**

参考：
- `agent_runtime_profile/.claude/agents/split-narration-segments.md`（narration 预处理）
- `agent_runtime_profile/.claude/agents/normalize-drama-script.md`（drama 预处理）

两者都遵循 `任务定义 / 核心原则 / 工作流程 / 输出格式 / 注意事项` 的骨架。

- [ ] **Step 4.2: 写 `split-reference-video-units.md`**

```markdown
---
name: split-reference-video-units
description: "参考生视频模式单集视频单元拆分 subagent（reference_video 模式专用）。使用场景：(1) project.generation_mode 或集级 generation_mode 为 reference_video，需要为某一集生成 step1_reference_units.md，(2) 用户要求重新拆分某集的参考视频单元，(3) manga-workflow 编排进入单集预处理阶段（reference_video 模式）。接收项目名、集数、本集小说文本路径，按「镜头连贯性 + 参考图齐全」拆分 video_unit，保存中间文件，返回摘要。"
---

你是一位专业的参考生视频单元架构师，专门将中文小说改编为适配多模态参考视频模型的 video_unit 表。每个 video_unit 对应一次视频生成调用，可含 1-4 个 shot。

## 任务定义

**输入**：主 agent 会在 prompt 中提供：
- 项目名称（如 `my_project`）
- 集数（如 `1`）
- 本集小说文件（如 `source/episode_1.txt`）
- 可用角色列表（`project.json.characters` 的名字）
- 可用场景列表（`project.json.scenes` 的名字）
- 可用道具列表（`project.json.props` 的名字）
- 单镜头支持时长列表（如 `[5, 8, 10]`）
- 模型最大参考图数（如 `9`）

**输出**：保存 `drafts/episode_{N}/step1_reference_units.md` 后，返回 unit 统计摘要。

## 核心原则

1. **跳过分镜**：不生成分镜图，直接按视频生成粒度（video_unit）拆分；每 unit = 一次生成调用。
2. **参考图驱动**：每个 unit 的描述只用 `@角色/@场景/@道具` 引用**已注册**的资产名；不写外貌/服装/场景细节（由参考图承担视觉一致性）。
3. **时长硬约束**：每 unit 所有 shot `duration` 之和不得超过**模型单次生成最大时长**（通常 8-15s）；总 references 数不得超过 `max_refs`。
4. **完成即返回**：独立完成全部工作后返回，不在中间步骤等待用户确认。

## 工作流程

### Step 1: 读取项目信息和小说原文

使用 Read 工具读取：
- `projects/{项目名}/project.json` — 获取 characters / scenes / props 三张表
- `projects/{项目名}/source/episode_{N}.txt` — 单集原文

### Step 2: 按 video_unit 粒度拆分

**拆分规则**：

- 每个 unit 对应一个**连贯的视频生成片段**：同一时间、同一地点、主体动作连续。
- 一个 unit 内可拆 1-4 个 shot；shot 表示镜头切换，但共享同一次生成调用。
- 单 shot 时长从支持列表中挑（默认 5s 或 8s）。多 shot 时合理分配，确保总和落在模型最大时长内。
- 时间/空间/情节重大切换点 → 开一个新 unit。
- 一个 unit 涉及的角色 / 场景 / 道具总数不得超过模型 `max_refs`；超出时将次要角色融入背景描述，不进入 references。

**描述规则**：

- 每 shot 的 `text` 字段用中文叙事，聚焦当下瞬间可见动作。
- 角色/场景/道具引用使用 `@名称`；名称必须来自 project.json 三张表。
- 严禁描写外貌、服装、场景色调、光影细节——这些由参考图提供。
- 严禁新增 project.json 中不存在的资产名。

**references 列表**：

- 按首次出现顺序登记；调整顺序决定发送给模型的 `[图N]` 编号。
- 每个 unit 的 references 是该 unit 所有 shot 中 `@` 提及的并集（去重）。

### Step 3: 保存中间文件

创建目录 `projects/{项目名}/drafts/episode_{N}/`（如不存在），
将 unit 表保存为 `step1_reference_units.md`，推荐格式：

```markdown
## 参考视频单元拆分结果

| unit_id | shots 数 | 总时长 | 涉及 references | shots 摘要 |
|---------|----------|--------|------------------|------------|
| E1U1 | 2 | 8s | character:主角, scene:酒馆 | Shot1(4s): @主角 推开酒馆门。Shot2(4s): 在 @酒馆 里环视。 |
| E1U2 | 1 | 5s | character:张三, prop:长剑 | Shot1(5s): @张三 抽出 @长剑。 |

### 完整 shot 文本（供 Step 2 使用）

#### E1U1

Shot 1 (4s): @主角 推开木门，屋内光线透出。
Shot 2 (4s): 他在 @酒馆 中央环视，目光停在对面。

#### E1U2

Shot 1 (5s): @张三 缓缓抽出 @长剑，剑刃映光。
```

使用 Write 工具写入文件。

### Step 4: 返回摘要

```
## 参考视频单元拆分完成（reference_video 模式）

**项目**: {项目名}  **第 N 集**

| 统计项 | 数值 |
|--------|------|
| 总 unit 数 | XX 个 |
| 总 shot 数 | XX 个 |
| 预计总时长 | X 分 X 秒 |
| 涉及角色 | XX 个 |
| 涉及场景 | XX 个 |
| 涉及道具 | XX 个 |
| references 最大数（单 unit） | XX / max_refs |

**文件已保存**: `drafts/episode_{N}/step1_reference_units.md`

下一步：主 agent 可 dispatch `create-episode-script` subagent 生成 JSON 剧本（ReferenceVideoScript）。
```

## 注意事项

- unit_id 从 `E{集数}U1` 开始按顺序递增。
- 每 unit shots 不超过 **4 个**；单 unit references 不超过 `max_refs`。
- 凡是 `@名称` 中的「名称」必须在主 agent 告诉你的 characters / scenes / props 三张表之一，否则不要使用；若确实需要新资产，应报告给主 agent 要求补资产生成。
- 时长的个位数选自主 agent 告知的 `supported_durations`；不要自己发明其它时长。
```

- [ ] **Step 4.3: 提交**

```bash
git add agent_runtime_profile/.claude/agents/split-reference-video-units.md
git commit -m "feat(agent): add split-reference-video-units subagent (PR6)"
```

---

## Task 5：扩展 `generate-video` skill CLI（检测 video_units 分派）

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py`
- Test: `tests/scripts/test_generate_video_reference_branch.py`

- [ ] **Step 5.1: 写失败测试**

新建 `tests/scripts/test_generate_video_reference_branch.py`：

```python
"""generate_video.py reference_video 分支单测。

验证 script 是 video_units 时会走 reference 路径（task_type="reference_video"）。
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# 允许直接 import skill 脚本
import sys
from importlib import util as _iu

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py"
spec = _iu.spec_from_file_location("_gvtest_generate_video", SCRIPT_PATH)
gv = _iu.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gv)


def _make_reference_project(tmp_path: Path) -> tuple[Path, str]:
    project_dir = tmp_path / "ref_proj"
    (project_dir / "videos").mkdir(parents=True)
    (project_dir / "scripts").mkdir()
    (project_dir / "reference_videos").mkdir()

    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "title": "t",
                "content_mode": "narration",  # 占位
                "generation_mode": "reference_video",
                "characters": {"主角": {"character_sheet": "characters/zhujue.png"}},
                "scenes": {"酒馆": {"scene_sheet": "scenes/jiuguan.png"}},
                "props": {},
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json", "generation_mode": "reference_video"}],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "content_mode": "reference_video",
                "title": "t",
                "summary": "s",
                "novel": {"title": "t", "chapter": "1"},
                "video_units": [
                    {
                        "unit_id": "E1U1",
                        "shots": [{"duration": 4, "text": "@主角 推门"}],
                        "references": [{"type": "character", "name": "主角"}],
                        "duration_seconds": 4,
                        "duration_override": False,
                        "transition_to_next": "cut",
                        "generated_assets": {"video_clip": None, "status": "pending"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project_dir, "ref_proj"


def test_detect_reference_video_script(tmp_path):
    project_dir, _ = _make_reference_project(tmp_path)
    script = json.loads((project_dir / "scripts" / "episode_1.json").read_text())
    assert gv.is_reference_video_script(script) is True


def test_detect_narration_script_not_reference():
    script = {"content_mode": "narration", "segments": [{"segment_id": "E1S1"}]}
    assert gv.is_reference_video_script(script) is False


def test_generate_episode_video_reference_enqueues_reference_tasks(tmp_path, monkeypatch):
    project_dir, project_name = _make_reference_project(tmp_path)

    monkeypatch.chdir(project_dir)

    captured: list[dict] = []

    def fake_batch(*, project_name, specs, on_success, on_failure):
        for spec_ in specs:
            on_success(
                gv.BatchTaskResult(
                    resource_id=spec_.resource_id,
                    task_id="t",
                    status="succeeded",
                    result={"file_path": f"reference_videos/{spec_.resource_id}.mp4"},
                    error=None,
                )
            )
            captured.append({"task_type": spec_.task_type, "resource_id": spec_.resource_id, "payload": spec_.payload})
        return [], []

    monkeypatch.setattr(gv, "batch_enqueue_and_wait_sync", fake_batch)

    # 创建伪 mp4 让 ffmpeg 拼接测试通过
    (project_dir / "reference_videos" / "E1U1.mp4").write_bytes(b"stub")

    gv.generate_episode_video("episode_1.json")

    assert captured == [
        {
            "task_type": "reference_video",
            "resource_id": "E1U1",
            "payload": {"script_file": "episode_1.json", "unit_id": "E1U1"},
        }
    ]
```

注：测试里我们 stub `batch_enqueue_and_wait_sync`，实际不触达后端。

- [ ] **Step 5.2: 运行测试确认失败**

```bash
uv run pytest tests/scripts/test_generate_video_reference_branch.py -v
```

预期：AttributeError `is_reference_video_script` 或 generate_episode_video 未识别 video_units。

- [ ] **Step 5.3: 修改 `generate_video.py`**

顶部加 helper：

```python
def is_reference_video_script(script: dict) -> bool:
    """检测脚本是否为参考生视频模式（看顶层 video_units / content_mode）。"""
    if script.get("content_mode") == "reference_video":
        return True
    return bool(script.get("video_units"))
```

新函数 `_build_reference_specs`：

```python
def _build_reference_specs(
    *,
    units: list[dict],
    script_filename: str,
    project_dir: Path,
    skip_ids: list[str] | None = None,
) -> tuple[list[BatchTaskSpec], dict[str, int]]:
    """reference_video 模式的 unit → BatchTaskSpec 映射。

    与 storyboard/grid 不同：不需要 storyboard_image；payload 仅传 script_file + unit_id。
    executor 侧（reference_video_tasks.py）会自己渲染 prompt + 压缩参考图。
    """
    skip_set = set(skip_ids or [])
    specs: list[BatchTaskSpec] = []
    order_map: dict[str, int] = {}
    for idx, unit in enumerate(units):
        unit_id = unit.get("unit_id") or f"U{idx}"
        if unit_id in skip_set:
            continue
        if (unit.get("generated_assets") or {}).get("video_clip"):
            print(f"  ✅ {unit_id} 已生成，跳过")
            continue
        if not unit.get("shots"):
            print(f"⚠️  {unit_id} 没有 shots，跳过")
            continue
        specs.append(
            BatchTaskSpec(
                task_type="reference_video",
                media_type="video",
                resource_id=unit_id,
                payload={
                    "script_file": script_filename,
                    "unit_id": unit_id,
                },
                script_file=script_filename,
            )
        )
        order_map[unit_id] = idx
    return specs, order_map
```

改造 `generate_episode_video()`：在加载 script 后分派：

```python
script = pm.load_script(project_name, script_filename)
episode = ProjectManager.resolve_episode_from_script(script, script_filename)

if is_reference_video_script(script):
    return _generate_reference_episode(
        project_name=project_name,
        project_dir=project_dir,
        script=script,
        script_filename=script_filename,
        episode=episode,
        resume=resume,
    )

# ...existing narration/drama logic...
```

新函数 `_generate_reference_episode()`：

```python
def _generate_reference_episode(
    *,
    project_name: str,
    project_dir: Path,
    script: dict,
    script_filename: str,
    episode: int,
    resume: bool,
) -> list[Path]:
    units = script.get("video_units") or []
    if not units:
        raise ValueError(f"第 {episode} 集 video_units 为空：{script_filename}")

    print(f"📋 第 {episode} 集共 {len(units)} 个 video_unit")

    # checkpoint 复用既有目录
    completed: list[str] = []
    started_at = datetime.now().isoformat()
    if resume:
        ckpt = load_checkpoint(project_dir, episode)
        if ckpt:
            completed = ckpt.get("completed_scenes", [])  # 字段名沿用

    output_dir = project_dir / "reference_videos"
    output_dir.mkdir(parents=True, exist_ok=True)

    ordered_paths: list[Path | None] = [None] * len(units)
    for idx, unit in enumerate(units):
        unit_id = unit.get("unit_id")
        candidate = output_dir / f"{unit_id}.mp4"
        if unit_id in completed and candidate.exists():
            ordered_paths[idx] = candidate

    specs, order_map = _build_reference_specs(
        units=units,
        script_filename=script_filename,
        project_dir=project_dir,
        skip_ids=[u for u in completed if (output_dir / f"{u}.mp4").exists()],
    )

    if specs:
        _submit_and_wait_with_checkpoint(
            project_name=project_name,
            project_dir=project_dir,
            specs=specs,
            order_map=order_map,
            ordered_paths=ordered_paths,
            completed_scenes=completed,
            save_fn=lambda: save_checkpoint(project_dir, episode, completed, started_at),
            item_type="unit",
        )

    final = [p for p in ordered_paths if p is not None]
    if not final:
        raise RuntimeError("没有生成任何 video_unit")

    clear_checkpoint(project_dir, episode)
    print(f"\n🎉 第 {episode} 集参考视频生成完成，共 {len(final)} 个 unit")
    return final
```

单场景/多场景入口（`--scene` / `--scenes` / `--all`）也需要按 `is_reference_video_script(script)` 分派到等价的 unit 生成函数：

```python
def generate_scene_video(script_filename, scene_id):
    # ... 读 script 后：
    if is_reference_video_script(script):
        return _generate_reference_unit(script_filename, scene_id)
    # ...existing...
```

`_generate_reference_unit()` 直接用 `enqueue_and_wait_sync(task_type="reference_video", media_type="video", resource_id=unit_id, payload={"script_file": script_filename, "unit_id": unit_id})` 等价于单场景版本。

> 关键：payload 至少要有 `script_file` + `unit_id` 两项，executor 会自行加载并解析 references / prompt。

- [ ] **Step 5.4: 运行测试确认通过**

```bash
uv run pytest tests/scripts/test_generate_video_reference_branch.py -v
```

预期：3 个测试 PASS。

再跑回归：

```bash
uv run pytest tests/scripts/ -v -k "video"  # 若存在旧测
```

- [ ] **Step 5.5: ruff 检查**

```bash
uv run ruff check agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py tests/scripts/test_generate_video_reference_branch.py
uv run ruff format agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py tests/scripts/test_generate_video_reference_branch.py
```

- [ ] **Step 5.6: 提交**

```bash
git add agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py tests/scripts/test_generate_video_reference_branch.py
git commit -m "feat(agent): generate_video dispatches reference_video units (PR6)"
```

---

## Task 6：更新 `generate-script` SKILL.md

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-script/SKILL.md`

- [ ] **Step 6.1: 改写前置条件 + 模式说明**

将原"前置条件"和"生成流程"中涉及 narration/drama 的描述扩展为三模式。替换成：

```markdown
## 前置条件

1. 项目目录下存在 `project.json`（包含 style、overview、characters、scenes、props）
2. 已完成 Step 1 预处理（按 `effective_mode` 选择一种中间文件）：
   - narration（图生视频/宫格生视频 + 说书）：`drafts/episode_N/step1_segments.md`
   - drama（图生视频/宫格生视频 + 剧集动画）：`drafts/episode_N/step1_normalized_script.md`
   - reference_video（参考生视频）：`drafts/episode_N/step1_reference_units.md`
```

在"生成流程"第 5 步里加第三分支：

```markdown
5. **Pydantic 验证** — 按 effective_mode 选 schema：
   - reference_video → `ReferenceVideoScript`（含 `video_units[]`）
   - narration → `NarrationEpisodeScript`
   - drama → `DramaEpisodeScript`
```

"输出格式"章节加：

```markdown
- reference_video 模式：`video_units` 数组（每个 unit 含 `shots[]`、`references[]`、`duration_seconds` 等）
- `metadata.total_units`（reference 模式）/ `total_segments`（narration）/ `total_scenes`（drama）
```

末尾将 `content-modes.md` 引用改为 `generation-modes.md`：

```markdown
> 三种生成模式的数据路径、预处理 subagent、schema 选择详见 `.claude/references/generation-modes.md`。
```

- [ ] **Step 6.2: 提交**

```bash
git add agent_runtime_profile/.claude/skills/generate-script/SKILL.md
git commit -m "docs(agent): generate-script SKILL.md covers reference_video branch"
```

---

## Task 7：更新 `generate-video` SKILL.md

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-video/SKILL.md`

- [ ] **Step 7.1: 插入检测分派段**

在文件开头"# 生成视频"之后加：

```markdown
## 模式自动分派

脚本在读取剧本后检测顶层结构，自动路由到对应 executor：

| 剧本特征 | 路由 | 输出目录 |
|---|---|---|
| `content_mode == "reference_video"` 或存在 `video_units[]` | `task_type="reference_video"` → `execute_reference_video_task` | `reference_videos/{unit_id}.mp4` |
| `segments[]`（narration） | `task_type="video"` → `execute_video_task` | `videos/scene_{segment_id}.mp4` |
| `scenes[]`（drama） | 同上 | `videos/scene_{scene_id}.mp4` |

参考模式跳过分镜图要求，直接把 `{script_file, unit_id}` 丢给 executor；executor 自行读取 unit.references → 从 characters/scenes/props 三 bucket 解析 sheet 图 → 内存压缩 → 渲染 prompt → 调 VideoBackend。
```

将"# 生成视频"下面一句原文：

> 使用 Veo 3.1 API 为每个场景/片段创建视频，以分镜图作为起始帧。

改为：

> 为每个场景/片段/unit 创建视频。storyboard/grid 模式用分镜图作为起始帧；reference_video 模式用角色/场景/道具参考图作为 `reference_images`，跳过分镜环节。

"## 生成前检查"补充：

```markdown
### reference_video 模式

- [ ] 所有 unit 引用的角色 / 场景 / 道具在 project.json 三 bucket 中已注册且 `*_sheet` 文件存在
- [ ] 每 unit shots 数 ≤ 4，总时长 ≤ 模型上限
- [ ] references 数 ≤ 模型 `max_reference_images`
```

命令行用法部分保持不变（同一个入口），但在末尾加 callout：

```markdown
> 参考生视频模式下，脚本输出命名为 `{unit_id}.mp4`，位于 `reference_videos/` 目录。
```

- [ ] **Step 7.2: 提交**

```bash
git add agent_runtime_profile/.claude/skills/generate-video/SKILL.md
git commit -m "docs(agent): generate-video SKILL.md covers reference_video branch"
```

---

## Task 8：改造 `manga-workflow` SKILL.md

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md`

- [ ] **Step 8.1: 状态检测加 reference_video 分支**

将"## 状态检测"第 3 项改为：

```markdown
3. 目标集 drafts/ 中间文件不存在？ → **阶段 3**
   - narration（generation_mode ∈ {storyboard, grid}）: `drafts/episode_{N}/step1_segments.md`
   - drama（generation_mode ∈ {storyboard, grid}）: `drafts/episode_{N}/step1_normalized_script.md`
   - reference_video: `drafts/episode_{N}/step1_reference_units.md`
```

第 6/7 项按 generation_mode 分派：

```markdown
6. **storyboard / grid 模式**：有场景缺少分镜图？ → **阶段 6**
   reference_video 模式：跳过此阶段（不生成分镜图）
7. 有场景/unit 缺少视频？ → **阶段 7**
```

- [ ] **Step 8.2: 阶段 3 加 reference_video 分支**

```markdown
## 阶段 3：单集预处理

**触发**：目标集的 drafts/ 中间文件不存在

根据 `effective_mode(project, episode)` 选择 subagent：

- generation_mode == `reference_video` → dispatch `split-reference-video-units`
- content_mode == `narration` → dispatch `split-narration-segments`
- content_mode == `drama` → dispatch `normalize-drama-script`

dispatch prompt 通用参数：项目名称、项目路径、集数、本集小说文件路径。
reference_video 额外参数：角色/场景/道具名称列表、`supported_durations`、`max_reference_images`。
```

- [ ] **Step 8.3: 阶段 6 按 generation_mode 分支**

将"## 阶段 6：分镜图生成"改为：

```markdown
## 阶段 6：分镜图生成（仅 storyboard / grid 模式）

**触发**：有场景缺少分镜图；**参考生视频模式跳过此阶段**

检查 `effective_mode(project, episode)`：

- `"storyboard"` → dispatch `generate-assets` (storyboard 命令：generate_storyboard.py)
- `"grid"` → dispatch `generate-assets` (grid 命令：generate_grid.py)
- `"reference_video"` → 不触发，直接跳到阶段 7

```

其余单个 dispatch 块（storyboard / grid）保留。删除文件里遗留的 `project.json.generation_mode == "single"` / `"grid"` 判断，统一由 `effective_mode()` 决定；"single" 语义由"storyboard"替代（与 spec §4.1 对齐）。

- [ ] **Step 8.4: 阶段 7 加 reference_video 命令**

```markdown
## 阶段 7：视频生成

**触发**：有场景/unit 缺少视频

**dispatch `generate-assets` subagent**：

reference_video 模式（脚本会自动按 `video_units` 分派）：

```
dispatch `generate-assets` subagent：
  任务类型：video
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json
  验证方式：重新读取 scripts/episode_{N}.json，检查各 unit.generated_assets.video_clip 字段
```

storyboard / grid 模式保持原命令与验证方式不变。
```

- [ ] **Step 8.5: 更新"内容模式规格"引用**

把 `> 内容模式规格（画面比例、时长等）详见 `.claude/references/content-modes.md`。` 改成：

```markdown
> 三种生成模式（图生视频 / 宫格生视频 / 参考生视频）的数据路径与阶段分支详见 `.claude/references/generation-modes.md`。
```

- [ ] **Step 8.6: 提交**

```bash
git add agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
git commit -m "docs(agent): manga-workflow branches on generation_mode (PR6)"
```

---

## Task 9：创建 `references/generation-modes.md`（取代 content-modes.md）

**Files:**
- Create: `agent_runtime_profile/.claude/references/generation-modes.md`
- Delete: `agent_runtime_profile/.claude/references/content-modes.md`

- [ ] **Step 9.1: 写 generation-modes.md**

```markdown
# 生成模式参考

ArcReel 支持三种**生成模式**（`generation_mode`）× 两种**内容模式**（`content_mode`），共五种可行路径（参考生视频不区分 narration/drama）。字段含义参见 Spec §4.1。

## 模式矩阵

| generation_mode | content_mode | 数据主结构 | 预处理 subagent | 脚本 schema | 视觉参考来源 |
|---|---|---|---|---|---|
| `storyboard` | `narration` | `segments[]` | split-narration-segments | NarrationEpisodeScript | 每片段一张分镜图作起始帧 |
| `storyboard` | `drama` | `scenes[]` | normalize-drama-script | DramaEpisodeScript | 每场景一张分镜图作起始帧 |
| `grid` | `narration` | `segments[]` + 宫格分组 | split-narration-segments | NarrationEpisodeScript | 宫格图切块 |
| `grid` | `drama` | `scenes[]` + 宫格分组 | normalize-drama-script | DramaEpisodeScript | 宫格图切块 |
| `reference_video` | `reference_video`（占位） | `video_units[]` | split-reference-video-units | ReferenceVideoScript | 角色 / 场景 / 道具 sheet 图直接作为 `reference_images` |

> `effective_mode(project, episode) = episode.generation_mode or project.generation_mode or "storyboard"`。缺省回退到图生视频（storyboard）。

## 阶段映射

```
Step 3 预处理
  reference_video    → dispatch split-reference-video-units
  narration          → dispatch split-narration-segments
  drama              → dispatch normalize-drama-script

Step 4 JSON 剧本
  → dispatch create-episode-script（内部按 generation_mode 选 schema）

Step 5 资产（characters / scenes / props 三类）
  三种模式共用 `generate-assets` skill（--characters/--scenes/--props）

Step 6 分镜图
  storyboard         → dispatch generate-assets (storyboard)
  grid               → dispatch generate-assets (grid)
  reference_video    → 跳过

Step 7 视频
  storyboard / grid  → dispatch generate-assets (video)
  reference_video    → dispatch generate-assets (video)
                       generate_video.py 检测 video_units 后路由到 task_type="reference_video"
```

## 视频规格

- **分辨率**：图片 1K，视频 1080p
- **单片段时长**（storyboard / grid）：项目 `default_duration`，narration 默认 4s、drama 默认 8s
- **单 unit 时长**（reference_video）：所有 shot 总和；单 shot 取值在模型 `supported_durations` 列表中
- **拼接**：全部模式用 ffmpeg concat；Veo extend 仅用于**单片段延长**，不串联不同镜头
- **BGM**：`negative_prompt` 自动排除

## Prompt 语言

- 图片/视频生成 prompt 使用**中文**
- 采用叙事式描述，不使用关键词罗列
- reference_video 模式额外规则：用 `@角色/@场景/@道具` 引用资产；**禁止**描写外貌、服装、场景细节（由参考图提供）

## 目录差异

```
projects/{name}/
├── storyboards/          # storyboard / grid 模式（分镜图）
├── grids/                # grid 模式（宫格图）
├── reference_videos/     # reference_video 模式视频输出
└── videos/               # storyboard / grid 模式视频输出
```

> 参考 `docs/google-genai-docs/nano-banana.md` 第 365 行起的 Prompting guide and strategies。
```

- [ ] **Step 9.2: 删除旧 content-modes.md**

```bash
git rm agent_runtime_profile/.claude/references/content-modes.md
```

- [ ] **Step 9.3: 全局搜索 content-modes.md 引用并替换**

```bash
```

使用 Grep：

```
pattern: content-modes\.md
path: agent_runtime_profile/
```

所有仍然指向 `content-modes.md` 的文件（generate-assets/SKILL.md 等）用 Edit 逐一改成 `generation-modes.md`。

- [ ] **Step 9.4: 提交**

```bash
git add agent_runtime_profile/.claude/references/generation-modes.md
git add agent_runtime_profile/.claude/references/content-modes.md  # 含 deletion
git add agent_runtime_profile/.claude/skills/  # 若被修改
git commit -m "docs(agent): replace content-modes.md with generation-modes.md (PR6)"
```

---

## Task 10：更新 `agent_runtime_profile/CLAUDE.md`

**Files:**
- Modify: `agent_runtime_profile/CLAUDE.md`

- [ ] **Step 10.1: 补 generation_mode 概念**

在"## 内容模式"之前（或紧随其后）新增一节：

```markdown
## 生成模式

系统支持三种**生成模式**（`generation_mode`），通过 `project.json` 顶层字段 + 集级 `episodes[i].generation_mode` 指定：

| generation_mode | 名称（UI） | 数据主结构 | 视觉参考来源 |
|---|---|---|---|
| `storyboard`（默认） | 图生视频 | `segments[]` 或 `scenes[]` + 分镜图 | 每片段一张分镜图作起始帧 |
| `grid` | 宫格生视频 | `segments[]` 或 `scenes[]` + 宫格分组 | 宫格图切块 |
| `reference_video` | 参考生视频 | `video_units[]` | 角色/场景/道具 sheet 图作为参考 |

解析规则：`effective_mode(project, episode) = episode.generation_mode or project.generation_mode or "storyboard"`。

> 完整模式矩阵与阶段分支详见 `.claude/references/generation-modes.md`。
```

- [ ] **Step 10.2: 更新"工作流程概览"**

将原 1-8 阶段改为按 mode 分支描述：

```markdown
## 工作流程概览

`/manga-workflow` 编排 skill 按以下阶段自动推进（每个阶段完成后等待用户确认）：

1. **项目设置**：创建项目、选择 `content_mode` + `generation_mode`、上传小说、生成项目概述
2. **全局角色/场景/道具提取** → dispatch `analyze-assets` subagent
3. **分集规划** → 主 agent 直接执行 peek+split 切分
4. **单集预处理** → 按 `effective_mode` 选：
   - reference_video → `split-reference-video-units`
   - narration → `split-narration-segments`
   - drama → `normalize-drama-script`
5. **JSON 剧本生成** → dispatch `create-episode-script`（按 mode 选 schema）
6. **资产设计（character/scene/prop 三类并行）** → dispatch `generate-assets`
7. **分镜图生成**：仅 `storyboard` / `grid` 模式；`reference_video` 跳过
8. **视频生成** → dispatch `generate-assets`（video）：脚本自动分派
```

- [ ] **Step 10.3: 更新"项目目录结构"**

```markdown
## 项目目录结构

\`\`\`
projects/{项目名}/
├── project.json
├── source/                # 原始小说内容
├── scripts/               # 分镜剧本 (JSON)
├── drafts/                # Step 1 中间文件
├── characters/            # 角色设计图
├── scenes/                # 场景设计图
├── props/                 # 道具设计图
├── storyboards/           # 分镜图片（storyboard / grid 模式）
├── grids/                 # 宫格图（grid 模式）
├── videos/                # 生成的视频片段（storyboard / grid 模式）
├── reference_videos/      # 生成的 video_unit（reference_video 模式）
├── thumbnails/            # 首帧缩略图
└── output/                # 最终输出
\`\`\`
```

- [ ] **Step 10.4: 更新 project.json 字段清单**

```markdown
### project.json 核心字段

- `schema_version`：项目数据格式版本（当前 1）
- `title`、`content_mode`（`narration`/`drama`）、`generation_mode`（`storyboard`/`grid`/`reference_video`）
- `style`、`style_description`
- `overview`：项目概述（synopsis、genre、theme、world_setting）
- `episodes`：剧集元数据（episode、title、script_file、可选 `generation_mode` 覆盖）
- `characters` / `scenes` / `props`：三类资产完整定义
```

- [ ] **Step 10.5: 更新"可用 Skills"与"架构"表**

在架构图 dispatch 列表加：

```
  ├─ dispatch → split-reference-video-units   参考模式 video_unit 拆分
```

在"可用 Skills"表里补一行：

```
| generate-video | `/generate-video` | 生成视频（自动分派 storyboard/grid/reference 模式） |
```

- [ ] **Step 10.6: 更新 `content-modes.md` 引用**

将 CLAUDE.md 里剩余的 `content-modes.md` 引用替换为 `generation-modes.md`。

- [ ] **Step 10.7: 提交**

```bash
git add agent_runtime_profile/CLAUDE.md
git commit -m "docs(agent): CLAUDE.md documents generation_mode + reference_video workflow (PR6)"
```

---

## Task 11：端到端 dry-run 联调

**Files:**
- Nothing new — pure verification.

- [ ] **Step 11.1: 构造一个 fixture reference 项目**

在本地 tmp 目录下建 `projects/demo_ref/`：
- `project.json` 带 `generation_mode="reference_video"`、三类资产各一条（sheet 图用占位 png）
- `source/episode_1.txt` 贴几段小说原文
- `drafts/episode_1/step1_reference_units.md` 先手工写（模拟 split 完成）

- [ ] **Step 11.2: 跑 generate_script dry-run**

```bash
cd projects/demo_ref
python ../../agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py --episode 1 --dry-run
```

预期：输出 prompt 含 `<step1_units>`、`video_units`、`@名称`。

- [ ] **Step 11.3: 手工产出 episode_1.json 并跑 generate_video**

手工写入 `scripts/episode_1.json`（带 `video_units`，每个 unit `generated_assets.video_clip` 为 null）。mock queue 或直接跑：

```bash
python ../../agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py episode_1.json
```

预期：日志打印 `task_type="reference_video"`（可先 monkeypatch / stub 避免真调 backend）。

若无法在本地 mock，至少保证 `is_reference_video_script(script)` 分支命中（可加一行 `print(...)` 临时验证，验证后移除）。

- [ ] **Step 11.4: 清理临时 demo 项目**

```bash
rm -rf projects/demo_ref
```

- [ ] **Step 11.5: 不提交（仅 smoke test）**

---

## Task 12：全量测试 + lint + i18n 校验

**Files:**
- Nothing new — pure verification.

- [ ] **Step 12.1: 跑完整测试套件**

```bash
uv run pytest -q --tb=short
```

预期：全绿，无回归；新模块覆盖率 ≥90%（查看 pytest 输出末尾）。

- [ ] **Step 12.2: 检查 `test_i18n_consistency.py`**

PR6 本身不加新的 i18n key（只改 Markdown 和 Python 逻辑）；但要确认 Task 5/2 的错误消息里没混入硬编码中文字符串。

```bash
uv run pytest tests/test_i18n_consistency.py -v
```

预期：PASS（不应该因为 PR6 失败）。若失败，定位硬编码串并迁到 `lib/i18n/{zh,en}/errors.py`。

- [ ] **Step 12.3: ruff 全库**

```bash
uv run ruff check lib/ agent_runtime_profile/.claude/skills/ tests/
uv run ruff format --check lib/ agent_runtime_profile/.claude/skills/ tests/
```

预期：无红字。

- [ ] **Step 12.4: markdown 人工抽查**

打开 `CLAUDE.md` / `generation-modes.md` / `split-reference-video-units.md` 的 rendered 视图（预览器或 VS Code），检查：
- 表格没串行
- 代码块未破坏
- 所有"content-modes.md"引用都已替换

- [ ] **Step 12.5: 提交（若有零星 format 修复）**

```bash
git add -p  # 选择性暂存 ruff format 调整
git commit -m "chore: ruff format fixes for PR6"
```

---

## 验收门槛（与 Roadmap §通用门槛一致）

- [x] 所有新增 test 通过，`ScriptGenerator` + `generate_video.py` 关键路径覆盖率 ≥ 90%
- [x] `uv run ruff check . && uv run ruff format .` 干净
- [x] 旧 storyboard / grid 项目零回归（narration + drama 测试继续绿）
- [x] i18n 一致性测试通过
- [x] PR 描述里列出本 PR 覆盖的 spec 章节：§7.1–§7.5

---

## 自检清单（写 PR 描述前过一遍）

- [ ] `split-reference-video-units.md` 里 description 字段准确触发（含 "reference_video"、"manga-workflow"、"参考视频"）。
- [ ] `generate_script.py` 在 effective_mode=reference_video 时读 `step1_reference_units.md`，校验用 `ReferenceVideoScript`。
- [ ] `generate_video.py` 通过 `is_reference_video_script()` 检测并入 `task_type="reference_video"` 队列，payload 至少含 `script_file` + `unit_id`。
- [ ] `manga-workflow/SKILL.md` 的阶段 3 / 6 / 7 均有 reference_video 分支；状态检测覆盖新 step1 文件。
- [ ] `CLAUDE.md` 的 generation_mode 章节、目录结构、Skills 表都已更新。
- [ ] `content-modes.md` 彻底删除，全部指向 `generation-modes.md`。
- [ ] 无新的硬编码中文字符串混入 lib/ 路径。

---

## 依赖关系 / 其他 worktree 注意事项

- PR4 前端工作在另一 worktree 进行；PR6 不改任何 `frontend/` 文件，merge 冲突应为零。
- PR6 依赖已合并的 PR2（script_models）+ PR3（reference_videos router/executor）；本 worktree 已包含两者。
- PR6 不改 `lib/generation_queue.py` / `lib/generation_worker.py`（PR3 已注册 `"reference_video"` task_type）。若在本 worktree 发现两者尚未注册，停止实施并联系 PR3 作者。
