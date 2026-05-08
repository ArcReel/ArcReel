# Agent Prompt 优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不动 schema 前提下，把分集节奏铁则、动态视觉规则、资产防崩与资产布局四类 prompt 增强通过新建 `lib/prompt_rules/` 模块分层注入到 `prompt_builders_script.py`、`generate_asset.py` 与 step1 subagent，让 ArcReel script 阶段输出对齐小云雀 V2.1 / Seedance 爆款 prompt 水平。

**Architecture:** 新建 `lib/prompt_rules/` 作为规则单一真相源；Python 端通过 `ARCREEL_PROMPT_RULES_V2` 灰度开关控制注入；subagent .md 静态贴文本，靠 substring 锚点测试防漂移；不改下游 generate-storyboard / generate-video，下游零改动自动受益。

**Tech Stack:** Python 3 + Pydantic + pytest（asyncio_mode=auto）+ ruff（line-length 120）+ uv 包管理

**Spec:** `docs/superpowers/specs/2026-05-07-agent-prompt-optimization-design.md`

---

## File Structure

**Create:**
- `lib/prompt_rules/__init__.py` — `is_v2_enabled()` 灰度开关
- `lib/prompt_rules/episode_pacing.py` — drama / narration 节奏铁则常量 + `render_pacing_section()`
- `lib/prompt_rules/visual_dynamic.py` — `IMAGE_DYNAMIC_PATCH` / `VIDEO_DYNAMIC_PATCH`
- `lib/prompt_rules/asset_anti_break.py` — `positive_for()` / `negative_for()`
- `lib/prompt_rules/asset_layout.py` — `layout_for()`
- `tests/prompt_rules/__init__.py` — 空文件
- `tests/prompt_rules/test_v2_switch.py`
- `tests/prompt_rules/test_episode_pacing.py`
- `tests/prompt_rules/test_visual_dynamic.py`
- `tests/prompt_rules/test_asset_anti_break.py`
- `tests/prompt_rules/test_asset_layout.py`
- `tests/prompt_rules/test_subagent_md_sync.py`
- `tests/test_prompt_builders_script_v2.py`
- `tests/test_generate_asset_prompt_wrap.py`

**Modify:**
- `lib/prompt_builders_script.py` — `build_drama_prompt`、`build_narration_prompt` 各注入两处
- `agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py` — `_build_specs` / `generate_single` 调用前 wrap description
- `agent_runtime_profile/.claude/agents/normalize-drama-script.md` — 贴 `DRAMA_PACING_RULES` 全文
- `agent_runtime_profile/.claude/agents/split-narration-segments.md` — 贴 `NARRATION_PACING_RULES` 全文

**Probe-only（无代码改动，仅记录）:**
- `lib/image_backends/{ark,gemini,grok,openai}.py` — 验证 negative_prompt 是否被消费

---

## Task 1: 建包 + 灰度开关

**Files:**
- Create: `lib/prompt_rules/__init__.py`
- Create: `tests/prompt_rules/__init__.py` (empty file, no content)
- Test: `tests/prompt_rules/test_v2_switch.py`

- [ ] **Step 1: 写失败测试**

`tests/prompt_rules/__init__.py`：

```python
```

（创建为空文件，让 pytest 识别为 package）

`tests/prompt_rules/test_v2_switch.py`：

```python
import pytest

from lib.prompt_rules import is_v2_enabled


def test_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCREEL_PROMPT_RULES_V2", raising=False)
    assert is_v2_enabled() is True


def test_explicit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    assert is_v2_enabled() is False


def test_off_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "OFF")
    assert is_v2_enabled() is False


def test_other_value_treated_as_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "true")
    assert is_v2_enabled() is True
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/prompt_rules/test_v2_switch.py -v
```

Expected: ImportError / ModuleNotFoundError on `lib.prompt_rules`.

- [ ] **Step 3: 写最小实现**

`lib/prompt_rules/__init__.py`：

```python
"""Prompt 规则单一真相源。

各规则模块（episode_pacing / visual_dynamic / asset_anti_break / asset_layout）
分别导出常量与 helper，由 prompt_builders_script.py 与 generate_asset.py 按需消费。
所有 Python 端注入受 `ARCREEL_PROMPT_RULES_V2` 环境变量控制（默认 on）。
"""

import os


def is_v2_enabled() -> bool:
    return os.environ.get("ARCREEL_PROMPT_RULES_V2", "on").lower() != "off"


__all__ = ["is_v2_enabled"]
```

- [ ] **Step 4: 跑测试确认通过 + ruff**

```bash
uv run pytest tests/prompt_rules/test_v2_switch.py -v
uv run ruff check lib/prompt_rules tests/prompt_rules && uv run ruff format lib/prompt_rules tests/prompt_rules
```

Expected: 4 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/prompt_rules/__init__.py tests/prompt_rules/__init__.py tests/prompt_rules/test_v2_switch.py
git commit -m "feat(prompt_rules): bootstrap 包 + ARCREEL_PROMPT_RULES_V2 灰度开关"
```

---

## Task 2: episode_pacing 模块

**Files:**
- Create: `lib/prompt_rules/episode_pacing.py`
- Test: `tests/prompt_rules/test_episode_pacing.py`

- [ ] **Step 1: 写失败测试**

`tests/prompt_rules/test_episode_pacing.py`：

```python
import pytest

from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
    render_pacing_section,
)


def test_drama_rules_keywords() -> None:
    text = render_pacing_section("drama")
    assert text == DRAMA_PACING_RULES
    assert "4 秒" in text
    assert "定格卡点" in text
    assert "15 秒" in text
    assert "Close-up" in text


def test_narration_rules_keywords() -> None:
    text = render_pacing_section("narration")
    assert text == NARRATION_PACING_RULES
    assert "4 秒" in text
    assert "钩子" in text
    assert "卡点留悬" in text


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown content_mode"):
        render_pacing_section("unknown")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/prompt_rules/test_episode_pacing.py -v
```

Expected: ModuleNotFoundError on `lib.prompt_rules.episode_pacing`.

- [ ] **Step 3: 写最小实现**

`lib/prompt_rules/episode_pacing.py`：

```python
"""分集节奏铁则（首镜 4 秒钩子 / 15 秒冲突节点 / 末镜定格卡点）。

注意：本模块的 DRAMA_PACING_RULES / NARRATION_PACING_RULES 文本会被
agent_runtime_profile/.claude/agents/normalize-drama-script.md 与
split-narration-segments.md 逐字镜像；漂移由 test_subagent_md_sync.py 防御。
"""

DRAMA_PACING_RULES = """
分集节奏铁则（请把以下要求体现到首镜与末镜的视觉描述上）：
- 开篇钩子：第 1 个分镜的 duration_seconds 设为 4 秒；该镜头画面必须以强视觉冲击/悬念/危机/极致反差作为焦点，杜绝静止介绍性远景。
- 中段冲突密度：每 15 秒至少出现 1 个冲突节点（动作转折 / 情绪反差 / 关系撕裂 / 异常事件），通过分镜的画面权重和镜头景别变化体现。
- 末镜定格卡点：本集最后一个分镜画面停在悬念升级或情绪极致瞬间，shot_type 推荐 Close-up 或 Extreme Close-up，禁止平稳收尾。
""".strip()


NARRATION_PACING_RULES = """
说书节奏要求：
- 首段画面对应朗读前 4 秒，必须用强视觉冲击 / 悬念 / 危机匹配钩子台词，杜绝平铺叙述。
- 末段画面服务于卡点留悬（特写人物 / 关键物件 / 极端表情），shot_type 推荐 Close-up 或 Extreme Close-up。
""".strip()


def render_pacing_section(content_mode: str) -> str:
    if content_mode == "drama":
        return DRAMA_PACING_RULES
    if content_mode == "narration":
        return NARRATION_PACING_RULES
    raise ValueError(f"unknown content_mode: {content_mode!r}")


__all__ = [
    "DRAMA_PACING_RULES",
    "NARRATION_PACING_RULES",
    "render_pacing_section",
]
```

- [ ] **Step 4: 跑测试 + ruff**

```bash
uv run pytest tests/prompt_rules/test_episode_pacing.py -v
uv run ruff check lib/prompt_rules tests/prompt_rules && uv run ruff format lib/prompt_rules tests/prompt_rules
```

Expected: 3 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/prompt_rules/episode_pacing.py tests/prompt_rules/test_episode_pacing.py
git commit -m "feat(prompt_rules): drama/narration 分集节奏铁则常量与 render"
```

---

## Task 3: visual_dynamic 模块

**Files:**
- Create: `lib/prompt_rules/visual_dynamic.py`
- Test: `tests/prompt_rules/test_visual_dynamic.py`

- [ ] **Step 1: 写失败测试**

`tests/prompt_rules/test_visual_dynamic.py`：

```python
from lib.prompt_rules.visual_dynamic import (
    IMAGE_DYNAMIC_PATCH,
    VIDEO_DYNAMIC_PATCH,
)


def test_image_patch_keywords() -> None:
    assert "微表情" in IMAGE_DYNAMIC_PATCH
    assert "物理飘动" in IMAGE_DYNAMIC_PATCH
    assert "环境必须是活的" in IMAGE_DYNAMIC_PATCH
    assert "内容融合" in IMAGE_DYNAMIC_PATCH
    assert "200 字以内" in IMAGE_DYNAMIC_PATCH


def test_video_patch_keywords() -> None:
    assert "肢体位移" in VIDEO_DYNAMIC_PATCH
    assert "微表情转换" in VIDEO_DYNAMIC_PATCH
    assert "物理环境互动" in VIDEO_DYNAMIC_PATCH
    assert "拒绝静态描写" in VIDEO_DYNAMIC_PATCH
    assert "150 字以内" in VIDEO_DYNAMIC_PATCH
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/prompt_rules/test_visual_dynamic.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: 写最小实现**

`lib/prompt_rules/visual_dynamic.py`：

```python
"""动态视觉规则——给 image_prompt.scene / video_prompt.action 字段说明追加的补丁。"""

IMAGE_DYNAMIC_PATCH = """
- 在描述静态画面时也必须暗示动态：发丝 / 衣摆 / 雨滴 / 落叶 / 尘埃 / 光斑等物理飘动元素至少出现一项。
- 必须包含可观察的微表情：眼神方向、瞳孔聚散、嘴角细微弧度、呼吸状态。
- 环境必须是活的：光影流转 / 雾气浮动 / 热浪扭曲 / 烛火摇曳，至少融入一项。
- 内容融合：禁止使用「画面基调:」「光影设定:」等标题式段落，所有元素融为一段连贯叙述。
- 单字段长度：scene 控制在 200 字以内。
""".strip()


VIDEO_DYNAMIC_PATCH = """
- 动作描述必须包含三层之一：肢体位移（角色在空间中的移动方向）/ 微表情转换（情绪从 A 到 B 的过渡）/ 物理环境互动（角色动作触发的环境反应：脚步扬尘 / 衣摆扫过桌面 / 推门带起气流）。
- 拒绝静态描写。即使是对话场景，也要描写说话人的呼吸节奏、手指小动作或视线偏移。
- 内容融合：把光影变化、氛围演变直接写进动作描述，而不是用独立标题。
- 单字段长度：action 控制在 150 字以内。
""".strip()


__all__ = ["IMAGE_DYNAMIC_PATCH", "VIDEO_DYNAMIC_PATCH"]
```

- [ ] **Step 4: 跑测试 + ruff**

```bash
uv run pytest tests/prompt_rules/test_visual_dynamic.py -v
uv run ruff check lib/prompt_rules tests/prompt_rules && uv run ruff format lib/prompt_rules tests/prompt_rules
```

Expected: 2 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/prompt_rules/visual_dynamic.py tests/prompt_rules/test_visual_dynamic.py
git commit -m "feat(prompt_rules): image/video 动态视觉补丁常量"
```

---

## Task 4: asset_anti_break 模块

**Files:**
- Create: `lib/prompt_rules/asset_anti_break.py`
- Test: `tests/prompt_rules/test_asset_anti_break.py`

- [ ] **Step 1: 写失败测试**

`tests/prompt_rules/test_asset_anti_break.py`：

```python
import pytest

from lib.prompt_rules.asset_anti_break import (
    NEGATIVE_BASE,
    negative_for,
    positive_for,
)


def test_positive_per_type_distinct() -> None:
    char = positive_for("character")
    scene = positive_for("scene")
    prop = positive_for("prop")
    assert char and scene and prop
    assert char != scene != prop != char


def test_positive_keywords() -> None:
    assert "五指" in positive_for("character")
    assert "对称" in positive_for("character")
    assert "透视" in positive_for("scene")
    assert "焦点" in positive_for("prop")


def test_negative_keywords() -> None:
    text = negative_for("character")
    assert "畸形" in text
    assert "断指" in text
    assert "乱码" in text


def test_negative_same_for_all_types() -> None:
    assert negative_for("character") == NEGATIVE_BASE
    assert negative_for("scene") == NEGATIVE_BASE
    assert negative_for("prop") == NEGATIVE_BASE


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown asset_type"):
        positive_for("unknown")
    with pytest.raises(ValueError, match="unknown asset_type"):
        negative_for("unknown")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/prompt_rules/test_asset_anti_break.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: 写最小实现**

`lib/prompt_rules/asset_anti_break.py`：

```python
"""资产防崩规则——正向（append 到 description 末尾）+ 负向（payload.negative_prompt）。"""

CHARACTER_POSITIVE = "人物五官对称、身体结构正常、手指完整为五指、肢体比例协调、面部特征清晰、服装造型完整无穿帮。"
SCENE_POSITIVE = "场景结构完整、空间透视正常、陈设固定、光影统一、无元素错位。"
PROP_POSITIVE = "道具结构完整、外观特征清晰、无变形扭曲、焦点明确。"

NEGATIVE_BASE = "畸形, 多肢体, 多指, 断指, 五官扭曲, 面部崩坏, 乱码文字, 水印, 模糊, 低分辨率, 穿帮元素, 严重色差"

_POSITIVE_MAP = {
    "character": CHARACTER_POSITIVE,
    "scene": SCENE_POSITIVE,
    "prop": PROP_POSITIVE,
}


def positive_for(asset_type: str) -> str:
    if asset_type not in _POSITIVE_MAP:
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    return _POSITIVE_MAP[asset_type]


def negative_for(asset_type: str) -> str:
    if asset_type not in _POSITIVE_MAP:
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    return NEGATIVE_BASE


__all__ = [
    "CHARACTER_POSITIVE",
    "SCENE_POSITIVE",
    "PROP_POSITIVE",
    "NEGATIVE_BASE",
    "positive_for",
    "negative_for",
]
```

- [ ] **Step 4: 跑测试 + ruff**

```bash
uv run pytest tests/prompt_rules/test_asset_anti_break.py -v
uv run ruff check lib/prompt_rules tests/prompt_rules && uv run ruff format lib/prompt_rules tests/prompt_rules
```

Expected: 5 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/prompt_rules/asset_anti_break.py tests/prompt_rules/test_asset_anti_break.py
git commit -m "feat(prompt_rules): 资产防崩正向/负向短语"
```

---

## Task 5: asset_layout 模块

**Files:**
- Create: `lib/prompt_rules/asset_layout.py`
- Test: `tests/prompt_rules/test_asset_layout.py`

- [ ] **Step 1: 写失败测试**

`tests/prompt_rules/test_asset_layout.py`：

```python
import pytest

from lib.prompt_rules.asset_layout import layout_for


def test_character_layout() -> None:
    text = layout_for("character")
    assert "三视图" not in text  # 我们用更具体的描述
    assert "正面" in text
    assert "侧面" in text


def test_scene_layout() -> None:
    text = layout_for("scene")
    assert "主画面" in text
    assert "细节" in text


def test_prop_layout() -> None:
    text = layout_for("prop")
    assert "正面" in text
    assert "45 度" in text
    assert "细节" in text


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown asset_type"):
        layout_for("unknown")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/prompt_rules/test_asset_layout.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: 写最小实现**

`lib/prompt_rules/asset_layout.py`：

```python
"""资产布局模板——按 asset_type 套三视图 / 主+细节 / 多视角。"""

CHARACTER_LAYOUT = "三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面。柔和均匀的摄影棚照明，无强烈阴影。"
SCENE_LAYOUT = "主画面占据四分之三区域展示环境整体外观与氛围，右下角小图为关键细节特写。柔和自然光线。"
PROP_LAYOUT = "三个视图水平排列在纯净浅灰背景上：正面全视图、45 度侧视图展示立体感、关键细节特写。柔和均匀的摄影棚照明，色彩准确。"

_LAYOUT_MAP = {
    "character": CHARACTER_LAYOUT,
    "scene": SCENE_LAYOUT,
    "prop": PROP_LAYOUT,
}


def layout_for(asset_type: str) -> str:
    if asset_type not in _LAYOUT_MAP:
        raise ValueError(f"unknown asset_type: {asset_type!r}")
    return _LAYOUT_MAP[asset_type]


__all__ = [
    "CHARACTER_LAYOUT",
    "SCENE_LAYOUT",
    "PROP_LAYOUT",
    "layout_for",
]
```

- [ ] **Step 4: 跑测试 + ruff**

```bash
uv run pytest tests/prompt_rules/test_asset_layout.py -v
uv run ruff check lib/prompt_rules tests/prompt_rules && uv run ruff format lib/prompt_rules tests/prompt_rules
```

Expected: 4 passed; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/prompt_rules/asset_layout.py tests/prompt_rules/test_asset_layout.py
git commit -m "feat(prompt_rules): 资产布局模板（character/scene/prop）"
```

---

## Task 6: 接入 prompt_builders_script.py

**Files:**
- Modify: `lib/prompt_builders_script.py:61-185` (build_narration_prompt) 与 `:188-312` (build_drama_prompt)
- Test: `tests/test_prompt_builders_script_v2.py`

- [ ] **Step 1: 写失败测试**

`tests/test_prompt_builders_script_v2.py`：

```python
"""验证 prompt_rules v2 注入是否正确接入两个 builder。"""

import pytest

from lib.prompt_builders_script import build_drama_prompt, build_narration_prompt
from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)
from lib.prompt_rules.visual_dynamic import (
    IMAGE_DYNAMIC_PATCH,
    VIDEO_DYNAMIC_PATCH,
)


def _kwargs() -> dict:
    return dict(
        project_overview={"synopsis": "S", "genre": "G", "theme": "T", "world_setting": "W"},
        style="动漫",
        style_description="日漫半厚涂",
        characters={"主角": {"description": "X"}},
        scenes={"庙宇": {"description": "Y"}},
        props={"玉佩": {"description": "Z"}},
        supported_durations=[4, 5, 6, 7, 8],
        default_duration=4,
    )


def test_drama_v2_on_injects_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert DRAMA_PACING_RULES in text
    assert IMAGE_DYNAMIC_PATCH in text
    assert VIDEO_DYNAMIC_PATCH in text


def test_drama_v2_off_omits_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert DRAMA_PACING_RULES not in text
    assert IMAGE_DYNAMIC_PATCH not in text
    assert VIDEO_DYNAMIC_PATCH not in text


def test_narration_v2_on_injects_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_narration_prompt(segments_md="| G01 | xxx | 25 | 4s | 否 | - |", **_kwargs())
    assert NARRATION_PACING_RULES in text
    assert IMAGE_DYNAMIC_PATCH in text
    assert VIDEO_DYNAMIC_PATCH in text


def test_narration_v2_off_omits_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    text = build_narration_prompt(segments_md="| G01 | xxx | 25 | 4s | 否 | - |", **_kwargs())
    assert NARRATION_PACING_RULES not in text
    assert IMAGE_DYNAMIC_PATCH not in text
    assert VIDEO_DYNAMIC_PATCH not in text


def test_drama_v2_on_keeps_camera_motion_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §4.6: 保留'每个片段仅选择一种镜头运动'约束不动。"""
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    text = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert "每个片段仅选择一种镜头运动" in text


def test_drama_v2_on_length_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """新版 prompt 不应膨胀超过旧版 + 3000 字符。"""
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    old = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    new = build_drama_prompt(scenes_md="| E1S01 | xxx | 4 | 剧情 | 是 |", **_kwargs())
    assert len(new) - len(old) < 3000
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_prompt_builders_script_v2.py -v
```

Expected: 6 个测试中 4 个 v2_on 用例失败（断言找不到注入文本）。

- [ ] **Step 3: 改 build_drama_prompt 与 build_narration_prompt**

在 `lib/prompt_builders_script.py` 顶部 import：

```python
from lib.prompt_rules import is_v2_enabled
from lib.prompt_rules.episode_pacing import render_pacing_section
from lib.prompt_rules.visual_dynamic import IMAGE_DYNAMIC_PATCH, VIDEO_DYNAMIC_PATCH
```

在 `build_narration_prompt` 函数体最开始（`character_names = ...` 之前）加：

```python
    pacing_block = render_pacing_section("narration") + "\n\n" if is_v2_enabled() else ""
    image_patch = "\n     " + IMAGE_DYNAMIC_PATCH.replace("\n", "\n     ") if is_v2_enabled() else ""
    video_patch = "\n     " + VIDEO_DYNAMIC_PATCH.replace("\n", "\n     ") if is_v2_enabled() else ""
```

把当前 prompt f-string 的开头：

```python
    prompt = f"""你的任务是为短视频生成分镜剧本。请仔细遵循以下指示：
```

改为：

```python
    prompt = f"""你的任务是为短视频生成分镜剧本。请仔细遵循以下指示：

{pacing_block}
```

把 `image_prompt` 字段说明里 `scene：` 段尾追加 `{image_patch}`：

定位 `lib/prompt_builders_script.py` 当前的：

```python
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。
```

改为（只在末行后追加 `{image_patch}`）：

```python
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。{image_patch}
```

把 `video_prompt` 的 `action` 段尾追加 `{video_patch}`：

```python
   - action：用中文精确描述该时长内主体的具体动作——身体移动、手势变化、表情转换。
     聚焦单一连贯动作，确保在指定时长内可完成。
     排除多场景切换、蒙太奇、快速剪辑等单次生成无法实现的效果。
     排除比喻性动作描述（如"像蝴蝶般飞舞"）。{video_patch}
```

`build_drama_prompt` 做完全相同的三处改动，区别仅是 `pacing_block = render_pacing_section("drama") + "\n\n" if is_v2_enabled() else ""`。

> 缩进 6 个空格是为了让 PATCH 文本与字段说明对齐。`replace("\n", "\n     ")` 把 PATCH 内部的换行也补缩进。

- [ ] **Step 4: 跑测试 + ruff**

```bash
uv run pytest tests/test_prompt_builders_script_v2.py -v
uv run ruff check lib/prompt_builders_script.py tests/test_prompt_builders_script_v2.py && uv run ruff format lib/prompt_builders_script.py tests/test_prompt_builders_script_v2.py
```

Expected: 6 passed; ruff clean.

- [ ] **Step 5: 跑全量回归确保未破坏旧 builder 测试**

```bash
uv run pytest tests/ -k "prompt_builders" -v
```

Expected: 全部 PASS（包含旧 builder 测试与新 v2 测试）。如旧测试报告新版本含意外文本，调整 patch 文案；不要修改旧测试。

- [ ] **Step 6: Commit**

```bash
git add lib/prompt_builders_script.py tests/test_prompt_builders_script_v2.py
git commit -m "feat(prompt): drama/narration builder 注入节奏铁则与动态视觉补丁"
```

---

## Task 7: image_backends negative_prompt 兼容性探测

**Files:**
- Read-only probe of `lib/image_backends/{ark,gemini,grok,openai}.py`
- Document findings in commit message + spec addendum

- [ ] **Step 1: 探测各 image backend 是否消费 negative_prompt**

```bash
grep -n "negative_prompt\|payload\.get\|\\*\\*payload" lib/image_backends/*.py
```

记录每个 backend 的处理方式：
- 接受并透传到 provider API → ✅ 支持
- 完全忽略 payload 中未知键 → ⚠️ 静默忽略（生成时不防崩，但不会崩）
- 报错拒绝未知键 → ❌ 必须先打通

- [ ] **Step 2: 验证 GenerationQueue / MediaGenerator 的 payload 透传链**

```bash
grep -n "payload" lib/generation_queue.py lib/generation_worker.py lib/media_generator.py | head -40
```

确认 `generate_asset.py` 写入 payload 的 `negative_prompt` 键能否流到 backend.generate(...)。

- [ ] **Step 3: 把探测结论写入 spec 第 9 章**

修改 `docs/superpowers/specs/2026-05-07-agent-prompt-optimization-design.md` 末尾「## 9. 已接受的代价」追加：

```markdown
- **image_backends negative_prompt 支持矩阵**（探测结果）：
  - ark: <yes/no/silent>
  - gemini: <yes/no/silent>
  - grok: <yes/no/silent>
  - openai: <yes/no/silent>
  
  对于 silent/no 的 backend，本期只走正向防崩；二期补 backend 适配。
```

- [ ] **Step 4: Commit（无代码改动，仅文档）**

```bash
git add docs/superpowers/specs/2026-05-07-agent-prompt-optimization-design.md
git commit -m "docs(spec): 补 image_backends negative_prompt 支持矩阵"
```

---

## Task 8: 接入 generate_asset.py（资产 description 包装）

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py:60-101` (`generate_single`、`_get_asset_description`) 与 `:133-166` (`_build_specs`)
- Test: `tests/test_generate_asset_prompt_wrap.py`

- [ ] **Step 1: 写失败测试**

`tests/test_generate_asset_prompt_wrap.py`：

```python
"""验证 generate_asset 的 description 包装行为。

测试只覆盖纯函数 _wrap_prompt，避开 ProjectManager / queue 副作用。
"""

import importlib
import sys
from pathlib import Path

import pytest

# 把 skill 脚本目录加入 sys.path（脚本不是 lib 包，需要这样导）
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "agent_runtime_profile/.claude/skills/generate-assets/scripts"
sys.path.insert(0, str(SCRIPT_DIR))

generate_asset = importlib.import_module("generate_asset")


def test_wrap_v2_on_appends_layout_and_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    prompt, neg = generate_asset._wrap_prompt("character", "二十岁青年，杏眼柳眉")
    assert "二十岁青年" in prompt
    assert "正面" in prompt  # layout
    assert "五指" in prompt  # positive 防崩
    assert neg is not None
    assert "畸形" in neg


def test_wrap_v2_off_returns_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "off")
    prompt, neg = generate_asset._wrap_prompt("character", "原始描述")
    assert prompt == "原始描述"
    assert neg is None


def test_wrap_each_type_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCREEL_PROMPT_RULES_V2", "on")
    char_p, _ = generate_asset._wrap_prompt("character", "X")
    scene_p, _ = generate_asset._wrap_prompt("scene", "X")
    prop_p, _ = generate_asset._wrap_prompt("prop", "X")
    # 每种 type 的 layout + positive 都不同
    assert char_p != scene_p != prop_p != char_p
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_generate_asset_prompt_wrap.py -v
```

Expected: AttributeError on `generate_asset._wrap_prompt`.

- [ ] **Step 3: 修改 generate_asset.py 加 _wrap_prompt 与调用点**

在 `agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py` 顶部 import：

```python
from lib.prompt_rules import is_v2_enabled
from lib.prompt_rules.asset_anti_break import negative_for, positive_for
from lib.prompt_rules.asset_layout import layout_for
```

在 `_get_asset_description` 函数后新增：

```python
def _wrap_prompt(asset_type: str, description: str) -> tuple[str, str | None]:
    """按 asset_type 给 description 追加 layout + 防崩短语；返回 (prompt, negative_prompt or None)。

    ARCREEL_PROMPT_RULES_V2=off 时退回原始 description（与 negative_prompt=None）。
    """
    if not is_v2_enabled():
        return description, None
    wrapped = f"{description}\n\n{layout_for(asset_type)}\n\n{positive_for(asset_type)}"
    return wrapped, negative_for(asset_type)
```

修改 `generate_single` 的 enqueue 块：

```python
    description = _get_asset_description(project, asset_type, name)
    if not description:
        raise ValueError(f"{cfg['label']} '{name}' 的描述为空或不存在于 project.json，请先添加描述")

    print(f"🎨 正在生成{cfg['label']}设计图: {name}")
    print(f"   描述: {description[:50]}..." if len(description) > 50 else f"   描述: {description}")

    prompt, neg = _wrap_prompt(asset_type, description)
    payload: dict = {"prompt": prompt}
    if neg is not None:
        payload["negative_prompt"] = neg

    queued = enqueue_and_wait(
        project_name=project_name,
        task_type=cfg["task_type"],
        media_type="image",
        resource_id=name,
        payload=payload,
        source="skill",
    )
```

修改 `_build_specs` 的列表推导：

```python
    specs: list[BatchTaskSpec] = []
    for name in resolved:
        prompt, neg = _wrap_prompt(asset_type, assets_dict[name]["description"])
        payload: dict = {"prompt": prompt}
        if neg is not None:
            payload["negative_prompt"] = neg
        specs.append(
            BatchTaskSpec(
                task_type=cfg["task_type"],
                media_type="image",
                resource_id=name,
                payload=payload,
            )
        )
    return specs
```

- [ ] **Step 4: 跑测试 + ruff**

```bash
uv run pytest tests/test_generate_asset_prompt_wrap.py -v
uv run ruff check agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py tests/test_generate_asset_prompt_wrap.py
uv run ruff format agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py tests/test_generate_asset_prompt_wrap.py
```

> ruff 配置 `exclude = [".worktrees", ".claude/worktrees"]`——确认 `agent_runtime_profile/.claude/...` 路径不在 exclude 范围内（路径里没有 `.worktrees` 和 `.claude/worktrees` 这两个**根级目录前缀**，应该 lint 得到）。如 ruff 跳过该路径，去掉 ruff 命令里的 generate_asset.py，仅手动按 line-length 120 + 字面规则核对。

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py tests/test_generate_asset_prompt_wrap.py
git commit -m "feat(generate-assets): description 自动追加 layout + 防崩，payload 带 negative_prompt"
```

---

## Task 9: 同步 normalize-drama-script.md

**Files:**
- Modify: `agent_runtime_profile/.claude/agents/normalize-drama-script.md`

- [ ] **Step 1: 读现状定位插入点**

```bash
grep -n "## 工作流程\|## 任务定义" agent_runtime_profile/.claude/agents/normalize-drama-script.md
```

预期：`## 工作流程` 在 24 行附近。

- [ ] **Step 2: 在「## 工作流程」之前插入节奏铁则段**

新段落（**逐字**对应 `lib/prompt_rules/episode_pacing.py:DRAMA_PACING_RULES`）：

```markdown
## 分集节奏铁则

分集节奏铁则（请把以下要求体现到首镜与末镜的视觉描述上）：
- 开篇钩子：第 1 个分镜的 duration_seconds 设为 4 秒；该镜头画面必须以强视觉冲击/悬念/危机/极致反差作为焦点，杜绝静止介绍性远景。
- 中段冲突密度：每 15 秒至少出现 1 个冲突节点（动作转折 / 情绪反差 / 关系撕裂 / 异常事件），通过分镜的画面权重和镜头景别变化体现。
- 末镜定格卡点：本集最后一个分镜画面停在悬念升级或情绪极致瞬间，shot_type 推荐 Close-up 或 Extreme Close-up，禁止平稳收尾。

拆分剧本时必须遵循上述铁则——首镜时长与末镜情绪卡点直接影响是否触发同步校验断言。

```

> 注意：节奏铁则段落的**第一行（"分集节奏铁则（请把..."）** 与 `DRAMA_PACING_RULES` 的首行完全一致；**最后一行（"末镜定格卡点：..."）** 也完全一致。Task 11 的同步测试用首尾各 30 字符锚点断言，所以这两行不能改字。

- [ ] **Step 3: Commit（不跑测试，Task 11 一并跑）**

```bash
git add agent_runtime_profile/.claude/agents/normalize-drama-script.md
git commit -m "docs(agent): normalize-drama-script 贴 DRAMA_PACING_RULES"
```

---

## Task 10: 同步 split-narration-segments.md

**Files:**
- Modify: `agent_runtime_profile/.claude/agents/split-narration-segments.md`

- [ ] **Step 1: 读现状定位插入点**

```bash
grep -n "## 工作流程\|## 任务定义" agent_runtime_profile/.claude/agents/split-narration-segments.md
```

- [ ] **Step 2: 在「## 工作流程」之前插入节奏铁则段**

新段落（**逐字**对应 `lib/prompt_rules/episode_pacing.py:NARRATION_PACING_RULES`）：

```markdown
## 说书节奏铁则

说书节奏要求：
- 首段画面对应朗读前 4 秒，必须用强视觉冲击 / 悬念 / 危机匹配钩子台词，杜绝平铺叙述。
- 末段画面服务于卡点留悬（特写人物 / 关键物件 / 极端表情），shot_type 推荐 Close-up 或 Extreme Close-up。

拆分片段时必须遵循上述铁则——首段画面权重与末段卡点是同步校验断言的检查项。

```

> 第一行（"说书节奏要求："）与最后一行（"末段画面..."）必须与 `NARRATION_PACING_RULES` 首尾完全一致。

- [ ] **Step 3: Commit**

```bash
git add agent_runtime_profile/.claude/agents/split-narration-segments.md
git commit -m "docs(agent): split-narration-segments 贴 NARRATION_PACING_RULES"
```

---

## Task 11: subagent .md 同步校验测试

**Files:**
- Test: `tests/prompt_rules/test_subagent_md_sync.py`

- [ ] **Step 1: 写测试**

`tests/prompt_rules/test_subagent_md_sync.py`：

```python
"""漂移防御：lib.prompt_rules.episode_pacing 的常量必须出现在对应 subagent .md 中。

用首尾 30 字符锚点做 substring 断言，避免空白差异误报。
"""

from pathlib import Path

from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)

REPO = Path(__file__).resolve().parents[2]


def _normalize(text: str) -> str:
    return "".join(text.split())


def test_drama_pacing_in_normalize_drama_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/normalize-drama-script.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(DRAMA_PACING_RULES)
    assert rules_norm[:60] in md_norm, "DRAMA_PACING_RULES 首段未在 normalize-drama-script.md 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, "DRAMA_PACING_RULES 末段未在 normalize-drama-script.md 中找到（漂移）"


def test_narration_pacing_in_split_narration_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/split-narration-segments.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(NARRATION_PACING_RULES)
    assert rules_norm[:60] in md_norm, "NARRATION_PACING_RULES 首段未在 split-narration-segments.md 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, "NARRATION_PACING_RULES 末段未在 split-narration-segments.md 中找到（漂移）"
```

> 用 `_normalize` 抹去所有空白后再比，对 markdown 渲染习惯（额外缩进、换行差异）有韧性；锚点 60 字符（≈30 中文字）在中文 prompt 里足够长且唯一。

- [ ] **Step 2: 跑测试确认通过**

```bash
uv run pytest tests/prompt_rules/test_subagent_md_sync.py -v
```

Expected: 2 passed（Task 9 和 Task 10 已经把文本同步进去了）。

如失败：诊断输出会指出哪个文件的哪段缺失，回到 Task 9 / 10 修正 .md 文本，使其与 episode_pacing.py 的首尾完全字面一致（同步校验仅在意"出现"，不在意完整全文）。

- [ ] **Step 3: ruff + Commit**

```bash
uv run ruff check tests/prompt_rules/test_subagent_md_sync.py && uv run ruff format tests/prompt_rules/test_subagent_md_sync.py
git add tests/prompt_rules/test_subagent_md_sync.py
git commit -m "test(prompt_rules): subagent .md 与 Python 常量漂移防御"
```

---

## Task 12: 全量回归 + 端到端 dry-run 验收

**Files:**
- 无代码改动，仅运行验证

- [ ] **Step 1: 全量 pytest**

```bash
uv run pytest -v
```

Expected: 全绿。如有非本 PR 引入的失败（旧测试不稳定），分别记录排查；本 PR 新增 / 修改的测试必须全绿。

- [ ] **Step 2: 准备 sandbox 项目**

如果已有可用项目（如 `projects/test0205`）则跳过；否则用最小占位项目：

```bash
# 用现有项目跑 dry-run 即可，不必新建
ls projects/ | head -5
```

挑一个有 `drafts/episode_1/step1_*.md` 的项目，记为 `$PROJ`。

- [ ] **Step 3: 旧 prompt 快照**

```bash
cd projects/$PROJ
ARCREEL_PROMPT_RULES_V2=off uv run python ../../agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py --episode 1 --dry-run > /tmp/prompt_old.txt
cd ../..
```

- [ ] **Step 4: 新 prompt 快照**

```bash
cd projects/$PROJ
ARCREEL_PROMPT_RULES_V2=on uv run python ../../agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py --episode 1 --dry-run > /tmp/prompt_new.txt
cd ../..
```

- [ ] **Step 5: diff 比对**

```bash
diff /tmp/prompt_old.txt /tmp/prompt_new.txt
```

人工确认：
1. 新版含「分集节奏铁则」（drama）或「说书节奏要求」（narration）整段
2. 新版 image_prompt.scene 字段说明含「微表情」「物理飘动」「内容融合」
3. 新版 video_prompt.action 字段说明含「肢体位移」「拒绝静态描写」
4. 新版长度 - 旧版长度 < 3000 字符
5. 现有「每个片段仅选择一种镜头运动」约束**仍在**

- [ ] **Step 6: 真 API 抽查（可选但强烈推荐）**

如果有可用 API 配额：

```bash
cd projects/$PROJ
ARCREEL_PROMPT_RULES_V2=on uv run python ../../agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py --episode 1
```

读 `scripts/episode_1.json`，人工抽查 3 个分镜：
- **首镜**：`duration_seconds == 4`，`image_prompt.scene` 含强冲击元素（暴雨/危机/反差等）
- **末镜**：`shot_type ∈ {Close-up, Extreme Close-up}`
- **任一中段**：`image_prompt.scene` 至少含微表情 / 物理飘动 / 环境互动其一

不通过 → 调整 `lib/prompt_rules/episode_pacing.py` / `visual_dynamic.py` 文案；调整后回到 Step 4 重跑。

- [ ] **Step 7: 资产生成抽查（可选）**

```bash
cd projects/$PROJ
ARCREEL_PROMPT_RULES_V2=on uv run python ../../agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py --type character --name <一个待生成角色>
```

人工看生成的 `character_sheet`：
- 三视图布局是否到位
- 五官 / 五指是否对称完整
- 与同角色多次生成的一致性是否更稳

- [ ] **Step 8: 验收清单 commit**

如有 prompt 文案微调，commit。如无：

```bash
git log --oneline feat/agent-prompt-optimization | head -15  # 确认提交链整齐
```

---

## Self-Review

**Spec 覆盖**

| Spec 章节 | 实现任务 |
|---|---|
| §3.1 模块结构（4 个 rule 模块） | Task 1（包+开关）、2（episode_pacing）、3（visual_dynamic）、4（asset_anti_break）、5（asset_layout） |
| §3.2 接入点（subagent / builder / generate_asset） | Task 6（builder）、8（generate_asset）、9（normalize-drama .md）、10（split-narration .md） |
| §3.3 灰度开关 | Task 1 实现 + Task 6/8 用例覆盖 on/off |
| §4.1 episode_pacing 内容（4秒/15秒/定格卡点） | Task 2 |
| §4.2 visual_dynamic 内容 | Task 3 |
| §4.3 asset_anti_break 内容 | Task 4 |
| §4.4 asset_layout 内容 | Task 5 |
| §4.5 generate_asset wrap 实现 | Task 8 |
| §4.6 builder 注入 + 保留 camera_motion 约束 | Task 6（含保留约束的回归断言） |
| §4.7 subagent .md 同步 | Task 9 + 10 + 11（漂移防御） |
| §5 端到端数据流验证 | Task 12 |
| §6.1 灰度开关回滚 | Task 1 实现，Task 6/8/12 用例验证 |
| §6.2 negative_prompt 通道未知 | Task 7 探测 |
| §6.3 漂移防御 | Task 11 |
| §6.4 Gemini 输出退化人工抽查 | Task 12 Step 6 |
| §7.1-7.4 单元/集成测试 | Task 1-6, 8 全覆盖 |
| §7.5 端到端 dry-run | Task 12 |

**类型一致性检查**

- `_wrap_prompt(asset_type, description) -> tuple[str, str | None]`：Task 8 测试用例与实现签名一致
- `is_v2_enabled() -> bool`：Task 1 / 6 / 8 全部使用同一签名
- `render_pacing_section(content_mode) -> str`：Task 2 + Task 6 调用方一致
- `positive_for / negative_for / layout_for(asset_type) -> str`：四个 helper 同形参签名
- `BatchTaskSpec(payload=dict)`：Task 8 修改与现有调用兼容（payload 是 dict，多加一个键不破坏）

**Placeholder 扫描**

- 无 TBD / TODO
- Task 7 文档化的"<yes/no/silent>"是探测后填入的真实结果，不是占位（执行者必须填具体值）
- 所有代码块都给出可粘贴的完整实现，无 "similar to Task N" 引用

**已知偏差**

- Task 8 的 ruff 命令对 `agent_runtime_profile/.claude/...` 路径可能被 ruff 配置 exclude 跳过——已在该任务里说明降级策略（手动核对 line-length）。
- 无其他已知问题。

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-agent-prompt-optimization.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 我每个任务派一个新鲜 subagent，任务间复核，迭代快

**2. Inline Execution** — 在当前会话用 executing-plans 批执行，按检查点复核

**Which approach?**
