# 新建项目向导 + 风格模版系统 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `CreateProjectModal` 重构为三步向导（基础信息 → 模型 → 风格），引入 36 条内置画风模版 + 缩略图，实现风格参考图与模版的互斥，旧项目懒迁移；概览页移除风格区块。

**Architecture:** 后端新增单一真相源 `lib/style_templates.py`（含 registry + legacy migration map）。后端路由 `CreateProjectRequest` 扩展模型字段与 `style_template_id`，空值不写入 project.json（"留空=用全局默认"）。前端拆出 `ModelConfigSection` 共享组件供向导 step 2 与 `ProjectSettingsPage` 复用。`CreateProjectModal` 内部以 `step: 1|2|3` 驱动三个子组件。36 张缩略图作为 `frontend/public/style-thumbnails/*.png` 静态资源。

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic / pytest | React 19 + TypeScript / vitest / wouter / zustand / Tailwind / i18next

## 参考设计

- Spec: `docs/superpowers/specs/2026-04-14-style-presets-design.md`
- 源 prompt 文本: `docs/生图画风前置提示词4.10.docx`
- 已生成的 36 张缩略图: `frontend/public/style-thumbnails/*.png`（未入库）

## 文件结构

### 后端

| 文件 | 操作 | 职责 |
|---|---|---|
| `lib/style_templates.py` | 新建 | 36 条 `STYLE_TEMPLATES` + `LEGACY_STYLE_MAP` + `resolve_template_prompt` |
| `lib/i18n/zh/templates.py` | 新建 | 中文 name / tagline |
| `lib/i18n/en/templates.py` | 新建 | 英文 name / tagline（直译首版） |
| `lib/prompt_utils.py` | 修改 | 删除 `STYLES` 与 `validate_style` |
| `lib/project_manager.py` | 修改 | 新增 `_migrate_legacy_style` + `load_project` 注入 + `create_project_metadata` 扩展 |
| `server/routers/projects.py` | 修改 | `CreateProjectRequest` 加字段，处理 `style_template_id` 展开 |
| `server/routers/files.py` | 修改 | 删除 `DELETE /style-image`、`PATCH /style-description` |
| `tests/test_style_templates.py` | 新建 | registry 完整性 / resolver / legacy map |
| `tests/test_project_manager_migration.py` | 新建 | `_migrate_legacy_style` 全场景 |
| `tests/test_projects_router.py` | 修改 | 新字段透传测试 |
| `tests/test_files_router.py` | 修改 | 删除对应测试 |
| `tests/test_prompt_utils.py` | 修改 | 删除 `validate_style` 测试 |

### 前端

| 文件 | 操作 | 职责 |
|---|---|---|
| `frontend/src/data/style-templates.ts` | 新建 | `STYLE_TEMPLATES` 清单（id/category/thumbnail） |
| `frontend/src/i18n/zh/templates.ts` | 新建 | 中文翻译 |
| `frontend/src/i18n/en/templates.ts` | 新建 | 英文翻译 |
| `frontend/src/i18n/config.ts` | 修改 | 注册 `templates` namespace |
| `frontend/src/api.ts` | 修改 | `createProject` 参数对象化 + 新字段；删除 `deleteStyleImage` / `updateStyleDescription` |
| `frontend/src/api.test.ts` | 修改 | 更新/删除对应测试 |
| `frontend/src/components/shared/ModelConfigSection.tsx` | 新建 | 视频/图片/3文本/时长 共享配置块 |
| `frontend/src/components/shared/ModelConfigSection.test.tsx` | 新建 | 默认提示 / duration 联动 |
| `frontend/src/components/pages/create-project/WizardStep1Basics.tsx` | 新建 | title/content_mode/aspect_ratio/generation_mode |
| `frontend/src/components/pages/create-project/WizardStep2Models.tsx` | 新建 | 封装 ModelConfigSection + 描述文案 |
| `frontend/src/components/pages/create-project/WizardStep3Style.tsx` | 新建 | tab + grid + custom upload |
| `frontend/src/components/pages/CreateProjectModal.tsx` | 重写 | 三步容器 + 状态 + 提交 |
| `frontend/src/components/pages/CreateProjectModal.test.tsx` | 重写 | 三步路径 / tab 切换 / duration 联动 |
| `frontend/src/components/canvas/OverviewCanvas.tsx` | 修改 | 删除"项目风格"区块 |
| `frontend/src/components/canvas/OverviewCanvas.test.tsx` | 修改 | 移除对应断言 |
| `frontend/src/components/pages/ProjectSettingsPage.tsx` | 修改 | 接入 ModelConfigSection |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | 修改 | 清理 project_style_* / 新增 templates 相关 key（若需跨 ns） |
| `frontend/public/style-thumbnails/*.png` | 入库 | 36 张已生成的缩略图 |

### 本地清理

- `projects/style-thumbnails/` 临时项目（已生成产物，本地可删）
- `.superpowers/brainstorm/*` 已 gitignored，无需清理

---

## Task 1: 后端 `lib/style_templates.py` 注册表

**Files:**
- Create: `lib/style_templates.py`
- Test: `tests/test_style_templates.py`

- [ ] **Step 1: 写失败测试 `tests/test_style_templates.py`**

```python
"""lib.style_templates 的测试。"""
import pytest

from lib.style_templates import (
    STYLE_TEMPLATES,
    LEGACY_STYLE_MAP,
    resolve_template_prompt,
    list_templates_by_category,
)


def test_templates_count_and_categories():
    assert len(STYLE_TEMPLATES) == 36
    lives = [t for t in STYLE_TEMPLATES.values() if t["category"] == "live"]
    anims = [t for t in STYLE_TEMPLATES.values() if t["category"] == "anim"]
    assert len(lives) == 18
    assert len(anims) == 18


def test_template_ids_unique_and_slug_shaped():
    for tpl_id, data in STYLE_TEMPLATES.items():
        assert tpl_id.startswith(("live_", "anim_")), tpl_id
        assert "prompt" in data and data["prompt"].strip()
        assert data["category"] in ("live", "anim")


def test_legacy_map_targets_exist():
    for legacy, tpl_id in LEGACY_STYLE_MAP.items():
        assert tpl_id in STYLE_TEMPLATES, f"{legacy} -> {tpl_id} 不在 registry"
    # 具体映射契约
    assert LEGACY_STYLE_MAP["Photographic"] == "live_premium_drama"
    assert LEGACY_STYLE_MAP["Anime"] == "anim_kyoto"
    assert LEGACY_STYLE_MAP["3D Animation"] == "anim_3d_cg"


def test_resolve_template_prompt_ok():
    prompt = resolve_template_prompt("live_premium_drama")
    assert "精品短剧" in prompt or "真人电视剧" in prompt


def test_resolve_template_prompt_unknown_raises():
    with pytest.raises(KeyError):
        resolve_template_prompt("no_such_id")


def test_list_templates_by_category():
    grouped = list_templates_by_category()
    assert set(grouped.keys()) == {"live", "anim"}
    assert len(grouped["live"]) == 18
    assert len(grouped["anim"]) == 18
    # 保持定义顺序（Python 3.7+ dict 有序）
    assert grouped["live"][0]["id"].startswith("live_")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_style_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.style_templates'`

- [ ] **Step 3: 实现 `lib/style_templates.py`**

```python
"""风格模版注册表（单一真相源）。

模版 id 命名规则：{category}_{slug}，category ∈ {live, anim}。
prompt 文本来自 docs/生图画风前置提示词4.10.docx。
"""

from __future__ import annotations

# 完整 36 条，顺序即 UI 展示顺序
STYLE_TEMPLATES: dict[str, dict] = {
    # ===== 真人 AI 漫剧 (18) =====
    "live_cinematic_ancient": {"category": "live", "prompt": "画风：精品古装真人短剧风格，专业打光，高质量电视剧质感"},
    "live_zhang_yimou":       {"category": "live", "prompt": "画风：参考张艺谋电影风格，极致用色，强烈构图，仪式感叙事"},
    "live_ancient_xianxia":   {"category": "live", "prompt": "画风：精品古装仙侠真人电视剧临江仙风格，美白滤镜，细腻真实的皮肤质感，精致打光，极致高清画质"},
    "live_premium_drama":     {"category": "live", "prompt": "画风：真人电视剧风格，精品短剧画风，大师级构图"},
    "live_cinema":            {"category": "live", "prompt": "画风：参考院线电影，真人电影风格，达芬奇专业调色，大师级构图，电影色调"},
    "live_spartan":           {"category": "live", "prompt": "画风：斯巴达勇士风格，角斗士风格，古装史诗风格，史诗级大片质感，戏剧性的光线，浓重的明暗对比"},
    "live_bladerunner":       {"category": "live", "prompt": "画风：银翼杀手2049风格，极简野蛮主义赛博朋克，只用一种颜色来统治画面，粗野主义巨物建筑，气象级的环境粒子，留白"},
    "live_got":               {"category": "live", "prompt": "画风：参考权力的游戏电视剧画风，冷色史诗写实，中世纪权谋氛围，粗粝真实质感，低饱和电影调色"},
    "live_breaking_bad":      {"category": "live", "prompt": "画风：参考绝命毒师电视剧画风，犯罪题材美学，南美风格滤镜，真实质感滤镜"},
    "live_kdrama":            {"category": "live", "prompt": "画风：韩剧偶像剧风格，干净高级的商业影像，柔光美颜，偶像剧式浪漫氛围"},
    "live_kurosawa":          {"category": "live", "prompt": "画风：黑泽明风格，高对比黑白质感，强烈自然元素（风雨尘），动态构图，戏剧化光影，人性史诗感"},
    "live_nolan":             {"category": "live", "prompt": "画风：诺兰风格，IMAX大画幅质感，冷蓝灰色调，极其锐利的画面，深沉严肃的氛围，精密的光线控制"},
    "live_tarantino":         {"category": "live", "prompt": "画风：昆汀风格，高对比度，暴力美学，大胆的构图"},
    "live_lynch":             {"category": "live", "prompt": "画风：大卫林奇风格，在看似平淡无奇的日常表象下，隐藏着极度诡异、荒诞、令人毛骨悚然的超现实梦魇"},
    "live_anderson":          {"category": "live", "prompt": "画风：韦斯安德森风格，糖果色马卡龙配色"},
    "live_wong":              {"category": "live", "prompt": "画风：王家卫风格，慵懒暧昧的氛围，颗粒感胶片，东方都市孤独美学"},
    "live_shaw":              {"category": "live", "prompt": "画风：参考港式武侠电视剧风格，邵氏电影风格，电影感"},
    "live_cyberpunk":         {"category": "live", "prompt": "画风：参考真人赛博朋克电影，电影质感，极致高清画质"},

    # ===== 动画 AI 漫剧 (18) =====
    "anim_3d_cg":             {"category": "anim", "prompt": "画风：3D、游戏CG，影视级、虚幻引擎渲染"},
    "anim_cn_3d":             {"category": "anim", "prompt": "画风：国风3D、影视级、虚幻引擎渲染"},
    "anim_kyoto":             {"category": "anim", "prompt": "画风：商业动画画风，柔和光影效果，轻柔的赛璐珞上色，柔和的漫射光线，清晰干净的细轮廓线条，参考京都动画作品，参考石立太一动画作品，2d动画"},
    "anim_arcane":            {"category": "anim", "prompt": "油画三渲二画风：参考《双城之战》(Fortiche / Arcane Style)画风"},
    "anim_us_3d":             {"category": "anim", "prompt": "画风：美式3D动画电影风格、影视级、虚幻引擎渲染"},
    "anim_ink_wushan":        {"category": "anim", "prompt": "画风：硬核传统2D水墨，视觉特点：保留生猛的毛笔枯笔笔触，张力拉满。参考《雾山五行》风格"},
    "anim_ink_papercut":      {"category": "anim", "prompt": "画风：硬核传统2D水墨/剪纸，视觉特点：保留生猛的毛笔枯笔笔触，色彩借鉴中国传统重彩，战斗动作如中国武术般行云流水，张力拉满。参考《雾山五行》风格"},
    "anim_felt":              {"category": "anim", "prompt": "画风：羊毛毡风格，定格动画，真实光影，极致细节，氛围感，故事感，大师级构图"},
    "anim_clay":              {"category": "anim", "prompt": "画风：黏土动画风格，定格动画，真实光影，大师级构图"},
    "anim_jp_horror":         {"category": "anim", "prompt": "画风：低饱和度色调，日式惊悚动画美学"},
    "anim_kr_webtoon":        {"category": "anim", "prompt": "画风：韩国网络漫画风格，半写实动漫风格，简洁柔和的线条画工，流畅的渐变阴影处理，肌肤呈现光泽感，采用柔色调色彩方案，营造浪漫光影效果，采用特写构图手法，营造浓郁的情感氛围，角色细节刻画精细"},
    "anim_zzz":               {"category": "anim", "prompt": "画风：次世代高精三渲二 (Next-Gen Cel-Shading 3D) Zenless Zone Zero style，极致干净的赛璐璐线条，结合3D的平滑运镜。面部阴影经过极其严格的法线调整，保证任何角度都唯美"},
    "anim_ghibli":            {"category": "anim", "prompt": "画风：参考吉卜力动画电影风格，宫崎骏动画风格"},
    "anim_demon_slayer":      {"category": "anim", "prompt": "画风：参考《鬼灭之刃》画风、参考Ufotable飞碟社画风，粗描边"},
    "anim_cyberpunk":         {"category": "anim", "prompt": "画风：参考动画赛博朋克电影，电影质感，极致高清画质"},
    "anim_bloodborne":        {"category": "anim", "prompt": "画风：参考血源诅咒画风，克苏鲁风格、哥特、写实阴暗、阴冷雾气、低饱和冷色调、虚幻引擎渲染"},
    "anim_itojunji":          {"category": "anim", "prompt": "画风：惊悚诡异风、线条锐利，参考伊藤润二动画，数字漫画笔触、轻微颗粒感、哑光质感，惊悚压抑、悬疑感"},
    "anim_90s_retro":         {"category": "anim", "prompt": "画风：参考渡边信一郎作品风格，参考神山健治作品，90年代日本复古动漫风格，上世纪九十年代日漫风格的动漫，层次感，线条清晰，迷人氛围"},
}


LEGACY_STYLE_MAP: dict[str, str] = {
    "Photographic": "live_premium_drama",
    "Anime": "anim_kyoto",
    "3D Animation": "anim_3d_cg",
}


def resolve_template_prompt(template_id: str) -> str:
    """查表取 prompt。未知 id 抛 KeyError（交给调用方转成 HTTPException）。"""
    return STYLE_TEMPLATES[template_id]["prompt"]


def is_known_template(template_id: str) -> bool:
    return template_id in STYLE_TEMPLATES


def list_templates_by_category() -> dict[str, list[dict]]:
    """按 category 分组，返回列表保持定义顺序。
    每项形如 {'id': 'live_xxx', 'prompt': '...'}。"""
    grouped: dict[str, list[dict]] = {"live": [], "anim": []}
    for tpl_id, data in STYLE_TEMPLATES.items():
        grouped[data["category"]].append({"id": tpl_id, "prompt": data["prompt"]})
    return grouped
```

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/test_style_templates.py -v`
Expected: 6 passed

- [ ] **Step 5: ruff + 提交**

```bash
uv run ruff check lib/style_templates.py tests/test_style_templates.py
uv run ruff format lib/style_templates.py tests/test_style_templates.py
git add lib/style_templates.py tests/test_style_templates.py
git commit -m "feat(style-templates): 新增 36 条内置画风模版 registry"
```

---

## Task 2: 后端 `_migrate_legacy_style` 懒迁移

**Files:**
- Modify: `lib/project_manager.py` (添加 `_migrate_legacy_style` + `load_project` 调用)
- Test: `tests/test_project_manager_migration.py` (新建)

- [ ] **Step 1: 写失败测试 `tests/test_project_manager_migration.py`**

```python
"""ProjectManager 懒迁移测试。"""
import json
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    return ProjectManager(tmp_path)


def _write_project(pm: ProjectManager, name: str, data: dict) -> Path:
    project_dir = pm.projects_root / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return project_dir


def test_migrates_photographic_to_live_premium_drama(pm: ProjectManager):
    _write_project(pm, "p1", {"title": "P1", "style": "Photographic"})
    data = pm.load_project("p1")
    assert data["style_template_id"] == "live_premium_drama"
    assert "真人电视剧" in data["style"] or "精品短剧" in data["style"]


def test_migrates_anime_to_kyoto(pm: ProjectManager):
    _write_project(pm, "p2", {"title": "P2", "style": "Anime"})
    data = pm.load_project("p2")
    assert data["style_template_id"] == "anim_kyoto"


def test_migrates_3d_animation_to_3d_cg(pm: ProjectManager):
    _write_project(pm, "p3", {"title": "P3", "style": "3D Animation"})
    data = pm.load_project("p3")
    assert data["style_template_id"] == "anim_3d_cg"


def test_prefers_style_image_over_template_when_both_present(pm: ProjectManager):
    _write_project(pm, "p4", {
        "title": "P4",
        "style": "Photographic",
        "style_image": "reference.png",
        "style_description": "已分析",
    })
    data = pm.load_project("p4")
    assert data["style_template_id"] is None
    assert data["style"] == ""
    assert data["style_image"] == "reference.png"


def test_unknown_legacy_value_untouched(pm: ProjectManager):
    _write_project(pm, "p5", {"title": "P5", "style": "某种自由文本"})
    data = pm.load_project("p5")
    assert "style_template_id" not in data  # 未写入
    assert data["style"] == "某种自由文本"


def test_already_migrated_project_idempotent(pm: ProjectManager):
    _write_project(pm, "p6", {
        "title": "P6",
        "style": "画风：真人电视剧风格，精品短剧画风，大师级构图",
        "style_template_id": "live_premium_drama",
    })
    data = pm.load_project("p6")
    assert data["style_template_id"] == "live_premium_drama"
    # 二次 load 不变
    data2 = pm.load_project("p6")
    assert data2 == data


def test_migration_persists_to_disk(pm: ProjectManager, tmp_path: Path):
    _write_project(pm, "p7", {"title": "P7", "style": "Photographic"})
    pm.load_project("p7")
    raw = json.loads((tmp_path / "p7" / "project.json").read_text(encoding="utf-8"))
    assert raw["style_template_id"] == "live_premium_drama"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_project_manager_migration.py -v`
Expected: 7 tests FAIL（`style_template_id` 不存在）

- [ ] **Step 3: 实现迁移函数**

在 `lib/project_manager.py` 顶部 import 区域追加：
```python
from lib.style_templates import LEGACY_STYLE_MAP, resolve_template_prompt
```

在类内（靠近 `load_project` 附近）添加方法：
```python
@staticmethod
def _migrate_legacy_style(project: dict) -> bool:
    """检测旧 style 值并就地迁移。返回是否发生了变更。"""
    if "style_template_id" in project:
        return False  # 已迁移
    legacy_value = project.get("style", "")
    if legacy_value not in LEGACY_STYLE_MAP:
        return False
    if project.get("style_image"):
        # 参考图优先：清空旧 style、template_id 置 None
        project["style_template_id"] = None
        project["style"] = ""
    else:
        new_id = LEGACY_STYLE_MAP[legacy_value]
        project["style_template_id"] = new_id
        project["style"] = resolve_template_prompt(new_id)
    return True
```

在 `load_project` 末尾（返回之前）追加：
```python
if self._migrate_legacy_style(project):
    # 只写回文件，不刷新 metadata 的 updated_at（避免污染时间戳）
    project_file = self.get_project_path(project_name) / "project.json"
    project_file.write_text(
        json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
    )
```

（如 load_project 当前结构不同，先 Read 全文本再精确编辑。）

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/test_project_manager_migration.py -v`
Expected: 7 passed

- [ ] **Step 5: 跑 project_manager 其余测试不回归**

Run: `uv run python -m pytest tests/test_project_manager_more.py tests/test_project_manager*.py -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
uv run ruff check lib/project_manager.py tests/test_project_manager_migration.py
uv run ruff format lib/project_manager.py tests/test_project_manager_migration.py
git add lib/project_manager.py tests/test_project_manager_migration.py
git commit -m "feat(project-manager): 旧 style 预设懒迁移到新模版"
```

---

## Task 3: 后端 `prompt_utils` 清理旧 STYLES

**Files:**
- Modify: `lib/prompt_utils.py`
- Modify: `tests/test_prompt_utils.py`

- [ ] **Step 1: 查找 `STYLES` / `validate_style` 外部引用**

Run:
```bash
rg -l "from lib.prompt_utils import.*STYLES\b|validate_style" --type py
```
Expected: 仅 `lib/prompt_utils.py` 自身 + `tests/test_prompt_utils.py`（若有其他使用者，任务扩展到覆盖它们）

- [ ] **Step 2: 删除 `lib/prompt_utils.py` 中的常量 + 函数**

删除：
```python
STYLES = ["Photographic", "Anime", "3D Animation"]
```
以及：
```python
def validate_style(style: str) -> bool:
    return style in STYLES
```

- [ ] **Step 3: 移除对应测试用例**

在 `tests/test_prompt_utils.py` 删除关于 `STYLES` / `validate_style` 的所有断言。若文件因此为空，删除整个文件但保留其他用例。

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/test_prompt_utils.py -v`
Expected: 剩余测试全 pass

- [ ] **Step 5: 提交**

```bash
uv run ruff check lib/prompt_utils.py tests/test_prompt_utils.py
git add lib/prompt_utils.py tests/test_prompt_utils.py
git commit -m "refactor(prompt-utils): 移除旧 STYLES 三选一约束"
```

---

## Task 4: 后端 `CreateProjectRequest` 扩展 + 模版展开

**Files:**
- Modify: `server/routers/projects.py`
- Modify: `lib/project_manager.py` (`create_project_metadata` 新增参数)
- Modify: `tests/test_projects_router.py`

- [ ] **Step 1: 写失败测试（`tests/test_projects_router.py` 新增用例）**

```python
def test_create_project_with_style_template_id_expands_prompt(client, pm, ...):
    resp = client.post("/api/v1/projects", json={
        "title": "模版项目",
        "name": "tpl-1",
        "style_template_id": "live_premium_drama",
        "content_mode": "drama",
        "aspect_ratio": "9:16",
    })
    assert resp.status_code == 200
    data = pm.project_data["tpl-1"]
    assert data["style_template_id"] == "live_premium_drama"
    assert "真人电视剧" in data["style"]


def test_create_project_with_unknown_template_id_returns_400(client, pm):
    resp = client.post("/api/v1/projects", json={
        "title": "坏模版",
        "name": "bad-1",
        "style_template_id": "no_such",
    })
    assert resp.status_code == 400


def test_create_project_with_model_fields_persists(client, pm):
    resp = client.post("/api/v1/projects", json={
        "title": "模型项目",
        "name": "m-1",
        "video_backend": "gemini/veo-3",
        "image_backend": "gemini/nano-banana",
        "text_backend_script": "gemini/gemini-2.5",
        "default_duration": 8,
    })
    assert resp.status_code == 200
    data = pm.project_data["m-1"]
    assert data["video_backend"] == "gemini/veo-3"
    assert data["image_backend"] == "gemini/nano-banana"
    assert data["text_backend_script"] == "gemini/gemini-2.5"
    assert data["default_duration"] == 8


def test_create_project_empty_model_fields_not_written(client, pm):
    resp = client.post("/api/v1/projects", json={
        "title": "空字段项目",
        "name": "e-1",
        "video_backend": "",
        "image_backend": None,
    })
    assert resp.status_code == 200
    data = pm.project_data["e-1"]
    assert "video_backend" not in data
    assert "image_backend" not in data
```

（精确测试结构根据现有 fixture 调整；FakeProjectManager 的 `create_project_metadata` 需支持新参数 —— 在 Step 3 补）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_projects_router.py -v -k "template_id or model_fields"`
Expected: FAIL

- [ ] **Step 3: 修改 `CreateProjectRequest`（`server/routers/projects.py`）**

在请求模型里加字段：
```python
class CreateProjectRequest(BaseModel):
    title: str
    name: str | None = None
    content_mode: str = "narration"
    aspect_ratio: str = "9:16"
    generation_mode: str = "single"
    default_duration: int | None = None
    # ===== 新增 =====
    style_template_id: str | None = None
    video_backend: str | None = None
    image_backend: str | None = None
    text_backend_script: str | None = None
    text_backend_overview: str | None = None
    text_backend_style: str | None = None
```

在 `create_project` handler 里：
```python
from lib.style_templates import is_known_template, resolve_template_prompt

def _sync():
    manager = get_project_manager()
    title = (req.title or "").strip()
    ...
    # 展开模版
    style_prompt = ""
    if req.style_template_id:
        if not is_known_template(req.style_template_id):
            raise HTTPException(
                status_code=400,
                detail=_t("unknown_style_template", template_id=req.style_template_id),
            )
        style_prompt = resolve_template_prompt(req.style_template_id)

    manager.create_project(name)
    manager.create_project_metadata(
        project_name=name,
        title=title or name,
        style=style_prompt,
        content_mode=req.content_mode,
        aspect_ratio=req.aspect_ratio,
        default_duration=req.default_duration,
        style_template_id=req.style_template_id,
        video_backend=req.video_backend or None,
        image_backend=req.image_backend or None,
        text_backend_script=req.text_backend_script or None,
        text_backend_overview=req.text_backend_overview or None,
        text_backend_style=req.text_backend_style or None,
    )
    ...
```

- [ ] **Step 4: 扩展 `create_project_metadata`（`lib/project_manager.py`）**

```python
def create_project_metadata(
    self,
    project_name: str,
    title: str | None = None,
    style: str | None = None,
    content_mode: str = "narration",
    aspect_ratio: str = "9:16",
    default_duration: int | None = None,
    generation_mode: str = "single",
    style_template_id: str | None = None,
    video_backend: str | None = None,
    image_backend: str | None = None,
    text_backend_script: str | None = None,
    text_backend_overview: str | None = None,
    text_backend_style: str | None = None,
) -> dict:
    project = {
        "title": title or project_name,
        "content_mode": content_mode,
        "aspect_ratio": aspect_ratio,
        "style": style or "",
        "generation_mode": generation_mode,
        "episodes": [],
        "characters": {},
        "clues": {},
    }
    if default_duration is not None:
        project["default_duration"] = default_duration
    if style_template_id is not None:
        project["style_template_id"] = style_template_id
    # 模型字段：非空才写
    for key, val in (
        ("video_backend", video_backend),
        ("image_backend", image_backend),
        ("text_backend_script", text_backend_script),
        ("text_backend_overview", text_backend_overview),
        ("text_backend_style", text_backend_style),
    ):
        if val:
            project[key] = val
    self._touch_metadata(project)
    self.save_project(project_name, project)
    return project
```

- [ ] **Step 5: 更新 `tests/test_projects_router.py` 中 FakeProjectManager.create_project_metadata 签名**

接受新 kwargs，持久化到 `self.project_data[name]`。

- [ ] **Step 6: 跑测试**

Run: `uv run python -m pytest tests/test_projects_router.py -v`
Expected: 所有测试 pass（含新增 4 条）

- [ ] **Step 7: 新增 i18n 错误 key**

在 `lib/i18n/zh/errors.py` 和 `en/errors.py` 添加：
- zh: `"unknown_style_template": "未知的风格模版: {template_id}"`
- en: `"unknown_style_template": "Unknown style template: {template_id}"`

- [ ] **Step 8: 提交**

```bash
uv run ruff check server/routers/projects.py lib/project_manager.py tests/test_projects_router.py lib/i18n
uv run ruff format server/routers/projects.py lib/project_manager.py tests/test_projects_router.py
git add server/routers/projects.py lib/project_manager.py tests/test_projects_router.py lib/i18n
git commit -m "feat(create-project): CreateProjectRequest 支持模版 id 与模型字段"
```

---

## Task 5: 后端清理两个 style-related endpoints

**Files:**
- Modify: `server/routers/files.py` (删除 DELETE /style-image 与 PATCH /style-description)
- Modify: `tests/test_files_router.py`

- [ ] **Step 1: 确认前端已无调用**

Run:
```bash
rg -l "deleteStyleImage|updateStyleDescription" frontend/src
```
Expected: 此刻应包含 OverviewCanvas.tsx（Task 16 移除）、api.ts（Task 9 移除）、api.test.ts。记录下来，本任务先不动前端。

- [ ] **Step 2: 删除端点 `DELETE /projects/{project_name}/style-image`**

定位 `server/routers/files.py` 中：
```python
@router.delete("/projects/{project_name}/style-image")
async def delete_style_image(...): ...
```
整体删除。同时删除仅它使用的辅助函数（若有）。

- [ ] **Step 3: 删除端点 `PATCH /projects/{project_name}/style-description`**

同上删除整个 handler。

- [ ] **Step 4: 删除 `tests/test_files_router.py` 对应测试**

grep 定位：`rg "delete_style_image|style_description" tests/test_files_router.py`。删除相关用例。

- [ ] **Step 5: 跑 files router 测试**

Run: `uv run python -m pytest tests/test_files_router.py -v`
Expected: 剩余 pass

- [ ] **Step 6: 提交**

```bash
uv run ruff check server/routers/files.py tests/test_files_router.py
git add server/routers/files.py tests/test_files_router.py
git commit -m "refactor(files-router): 移除未使用的 style-image/description 修改端点"
```

---

## Task 6: 后端 i18n templates namespace

**Files:**
- Create: `lib/i18n/zh/templates.py`
- Create: `lib/i18n/en/templates.py`
- Modify: `lib/i18n/zh/__init__.py`（注册）
- Modify: `lib/i18n/en/__init__.py`

- [ ] **Step 1: 查看 i18n 包结构**

Run: `ls lib/i18n/zh/ lib/i18n/en/`
确定 namespace 注册方式（现存的 `errors.py` / `providers.py` 是如何被主调用的）。

- [ ] **Step 2: 创建 `lib/i18n/zh/templates.py`**

```python
"""风格模版的中文名称与标语。"""

TRANSLATIONS = {
    # ===== 真人 =====
    "name": {
        "live_cinematic_ancient": "精品古装",
        "live_zhang_yimou": "张艺谋风格",
        "live_ancient_xianxia": "古装仙侠",
        "live_premium_drama": "精品短剧",
        "live_cinema": "院线电影",
        "live_spartan": "斯巴达史诗",
        "live_bladerunner": "银翼杀手",
        "live_got": "权力的游戏",
        "live_breaking_bad": "绝命毒师",
        "live_kdrama": "韩剧偶像",
        "live_kurosawa": "黑泽明",
        "live_nolan": "诺兰",
        "live_tarantino": "昆汀",
        "live_lynch": "大卫林奇",
        "live_anderson": "韦斯安德森",
        "live_wong": "王家卫",
        "live_shaw": "邵氏武侠",
        "live_cyberpunk": "真人赛博朋克",
        # ===== 动画 =====
        "anim_3d_cg": "3D 游戏 CG",
        "anim_cn_3d": "国风 3D",
        "anim_kyoto": "商业动画 京都",
        "anim_arcane": "油画三渲二",
        "anim_us_3d": "美式 3D 动画",
        "anim_ink_wushan": "硬核水墨",
        "anim_ink_papercut": "水墨剪纸",
        "anim_felt": "羊毛毡",
        "anim_clay": "黏土定格",
        "anim_jp_horror": "日式惊悚",
        "anim_kr_webtoon": "韩漫风格",
        "anim_zzz": "次世代三渲二",
        "anim_ghibli": "吉卜力",
        "anim_demon_slayer": "鬼灭 Ufotable",
        "anim_cyberpunk": "动画赛博朋克",
        "anim_bloodborne": "血源克苏鲁",
        "anim_itojunji": "伊藤润二",
        "anim_90s_retro": "90 年代日漫",
    },
    "tagline": {
        "live_cinematic_ancient": "专业打光 · 电视剧质感",
        "live_zhang_yimou": "极致用色 · 仪式感",
        "live_ancient_xianxia": "临江仙 · 美白滤镜",
        "live_premium_drama": "真人电视剧 · 大师构图",
        "live_cinema": "达芬奇调色 · 电影色调",
        "live_spartan": "角斗士 · 浓重明暗",
        "live_bladerunner": "极简野蛮 · 赛博粒子",
        "live_got": "冷色史诗 · 权谋",
        "live_breaking_bad": "南美滤镜 · 犯罪美学",
        "live_kdrama": "柔光美颜 · 浪漫偶像",
        "live_kurosawa": "黑白高对比 · 人性史诗",
        "live_nolan": "IMAX · 冷蓝灰",
        "live_tarantino": "高对比 · 暴力美学",
        "live_lynch": "平淡日常 · 超现实",
        "live_anderson": "糖果色 · 马卡龙",
        "live_wong": "胶片颗粒 · 东方都市",
        "live_shaw": "港式武侠 · 电影感",
        "live_cyberpunk": "电影质感 · 极致高清",
        "anim_3d_cg": "影视级 · 虚幻引擎",
        "anim_cn_3d": "影视级 · 虚幻引擎",
        "anim_kyoto": "柔和赛璐珞 · 石立太一",
        "anim_arcane": "双城之战 · Arcane",
        "anim_us_3d": "影视级 · 皮克斯",
        "anim_ink_wushan": "雾山五行 · 枯笔",
        "anim_ink_papercut": "雾山五行 · 重彩",
        "anim_felt": "定格 · 真实光影",
        "anim_clay": "定格 · 大师构图",
        "anim_jp_horror": "低饱和 · 动画美学",
        "anim_kr_webtoon": "半写实 · 浪漫光影",
        "anim_zzz": "绝区零 · 法线调整",
        "anim_ghibli": "宫崎骏 · 温暖质感",
        "anim_demon_slayer": "粗描边 · 战斗番",
        "anim_cyberpunk": "电影质感 · 高清",
        "anim_bloodborne": "哥特 · 阴冷雾气",
        "anim_itojunji": "惊悚诡异 · 线条锐利",
        "anim_90s_retro": "渡边信一郎 · 神山健治",
    },
    "category_custom": "自定义",
    "category_live": "AI 真人剧",
    "category_anim": "AI 漫剧",
}
```

- [ ] **Step 3: 创建 `lib/i18n/en/templates.py`**

直译对应 key（name 用拼音/意译皆可，tagline 用简短英文描述）。内容略长，照 zh 结构填即可。

- [ ] **Step 4: 在 `lib/i18n/zh/__init__.py` 和 `en/__init__.py` 注册新 namespace**

（仿照 `errors` 的注册模式）

- [ ] **Step 5: 跑 i18n 一致性测试**

Run: `uv run python -m pytest tests/test_i18n_consistency.py -v`
Expected: pass（英文 key 齐全即可）

- [ ] **Step 6: 提交**

```bash
git add lib/i18n/zh/templates.py lib/i18n/en/templates.py lib/i18n/zh/__init__.py lib/i18n/en/__init__.py
git commit -m "i18n(backend): 新增 templates namespace（36 模版 × 2 语言）"
```

---

## Task 7: 前端 `style-templates.ts` 数据模块

**Files:**
- Create: `frontend/src/data/style-templates.ts`

- [ ] **Step 1: 创建模块**

```ts
/** 风格模版前端清单（id + category + thumbnail，prompt 由后端展开）。 */
export type StyleCategory = "live" | "anim";

export interface StyleTemplate {
  id: string;
  category: StyleCategory;
  thumbnail: string;  // 静态资源 URL
}

export const STYLE_TEMPLATES: StyleTemplate[] = [
  // ===== 真人 =====
  { id: "live_cinematic_ancient", category: "live", thumbnail: "/style-thumbnails/live_cinematic_ancient.png" },
  { id: "live_zhang_yimou",       category: "live", thumbnail: "/style-thumbnails/live_zhang_yimou.png" },
  { id: "live_ancient_xianxia",   category: "live", thumbnail: "/style-thumbnails/live_ancient_xianxia.png" },
  { id: "live_premium_drama",     category: "live", thumbnail: "/style-thumbnails/live_premium_drama.png" },
  { id: "live_cinema",            category: "live", thumbnail: "/style-thumbnails/live_cinema.png" },
  { id: "live_spartan",           category: "live", thumbnail: "/style-thumbnails/live_spartan.png" },
  { id: "live_bladerunner",       category: "live", thumbnail: "/style-thumbnails/live_bladerunner.png" },
  { id: "live_got",               category: "live", thumbnail: "/style-thumbnails/live_got.png" },
  { id: "live_breaking_bad",      category: "live", thumbnail: "/style-thumbnails/live_breaking_bad.png" },
  { id: "live_kdrama",            category: "live", thumbnail: "/style-thumbnails/live_kdrama.png" },
  { id: "live_kurosawa",          category: "live", thumbnail: "/style-thumbnails/live_kurosawa.png" },
  { id: "live_nolan",             category: "live", thumbnail: "/style-thumbnails/live_nolan.png" },
  { id: "live_tarantino",         category: "live", thumbnail: "/style-thumbnails/live_tarantino.png" },
  { id: "live_lynch",             category: "live", thumbnail: "/style-thumbnails/live_lynch.png" },
  { id: "live_anderson",          category: "live", thumbnail: "/style-thumbnails/live_anderson.png" },
  { id: "live_wong",              category: "live", thumbnail: "/style-thumbnails/live_wong.png" },
  { id: "live_shaw",              category: "live", thumbnail: "/style-thumbnails/live_shaw.png" },
  { id: "live_cyberpunk",         category: "live", thumbnail: "/style-thumbnails/live_cyberpunk.png" },
  // ===== 动画 =====
  { id: "anim_3d_cg",             category: "anim", thumbnail: "/style-thumbnails/anim_3d_cg.png" },
  { id: "anim_cn_3d",             category: "anim", thumbnail: "/style-thumbnails/anim_cn_3d.png" },
  { id: "anim_kyoto",             category: "anim", thumbnail: "/style-thumbnails/anim_kyoto.png" },
  { id: "anim_arcane",            category: "anim", thumbnail: "/style-thumbnails/anim_arcane.png" },
  { id: "anim_us_3d",             category: "anim", thumbnail: "/style-thumbnails/anim_us_3d.png" },
  { id: "anim_ink_wushan",        category: "anim", thumbnail: "/style-thumbnails/anim_ink_wushan.png" },
  { id: "anim_ink_papercut",      category: "anim", thumbnail: "/style-thumbnails/anim_ink_papercut.png" },
  { id: "anim_felt",              category: "anim", thumbnail: "/style-thumbnails/anim_felt.png" },
  { id: "anim_clay",              category: "anim", thumbnail: "/style-thumbnails/anim_clay.png" },
  { id: "anim_jp_horror",         category: "anim", thumbnail: "/style-thumbnails/anim_jp_horror.png" },
  { id: "anim_kr_webtoon",        category: "anim", thumbnail: "/style-thumbnails/anim_kr_webtoon.png" },
  { id: "anim_zzz",               category: "anim", thumbnail: "/style-thumbnails/anim_zzz.png" },
  { id: "anim_ghibli",            category: "anim", thumbnail: "/style-thumbnails/anim_ghibli.png" },
  { id: "anim_demon_slayer",      category: "anim", thumbnail: "/style-thumbnails/anim_demon_slayer.png" },
  { id: "anim_cyberpunk",         category: "anim", thumbnail: "/style-thumbnails/anim_cyberpunk.png" },
  { id: "anim_bloodborne",        category: "anim", thumbnail: "/style-thumbnails/anim_bloodborne.png" },
  { id: "anim_itojunji",          category: "anim", thumbnail: "/style-thumbnails/anim_itojunji.png" },
  { id: "anim_90s_retro",         category: "anim", thumbnail: "/style-thumbnails/anim_90s_retro.png" },
];

export const DEFAULT_TEMPLATE_ID = "live_premium_drama";

export function getTemplatesByCategory(cat: StyleCategory): StyleTemplate[] {
  return STYLE_TEMPLATES.filter((t) => t.category === cat);
}
```

- [ ] **Step 2: typecheck**

Run: `cd frontend && pnpm tsc --noEmit`
Expected: 通过

- [ ] **Step 3: 提交**

```bash
git add frontend/src/data/style-templates.ts
git commit -m "feat(frontend): 风格模版前端清单"
```

---

## Task 8: 前端 i18n templates namespace

**Files:**
- Create: `frontend/src/i18n/zh/templates.ts`
- Create: `frontend/src/i18n/en/templates.ts`
- Modify: `frontend/src/i18n/config.ts` / i18n 初始化（注册 namespace）

- [ ] **Step 1: 查看 i18n 注册方式**

Run: `cat frontend/src/i18n/index.ts 2>/dev/null; cat frontend/src/i18n/config.ts 2>/dev/null`
记录现有 namespace 如何 import。

- [ ] **Step 2: 创建 `frontend/src/i18n/zh/templates.ts`**

```ts
export default {
  category: {
    custom: "自定义",
    live: "AI 真人剧",
    anim: "AI 漫剧",
  },
  name: {
    live_cinematic_ancient: "精品古装",
    live_zhang_yimou: "张艺谋风格",
    live_ancient_xianxia: "古装仙侠",
    live_premium_drama: "精品短剧",
    // ... 其余同 Task 6 的 zh 数据
  },
  tagline: {
    live_cinematic_ancient: "专业打光 · 电视剧质感",
    // ... 同 Task 6
  },
  default_hint: "默认模型可在「项目大厅 → 设置 → 模型选择」中调整",
  current_global_default: "当前全局默认：{{value}}",
  use_global_default: "使用全局默认",
  model_video: "视频模型",
  model_image: "图片模型",
  model_text_script: "剧本生成模型",
  model_text_overview: "概述生成模型",
  model_text_style: "风格分析模型",
  duration_label: "默认时长",
  duration_auto: "auto",
  tab_custom_desc: "上传一张风格参考图，AI 会自动分析。选择此 tab 将清空模版选择。",
  upload_reference: "上传风格参考图",
  supported_formats: "PNG / JPG / WEBP",
  template_selected_default: "（默认）",
  wizard_step_basics: "基础信息",
  wizard_step_models: "模型",
  wizard_step_style: "风格",
  next_step: "下一步",
  prev_step: "上一步",
} as const;
```

- [ ] **Step 3: 创建 `frontend/src/i18n/en/templates.ts`**

照搬结构、直译，不赘述。

- [ ] **Step 4: 注册 namespace**

按已观察的注册模式更新 i18n 入口。

- [ ] **Step 5: typecheck**

Run: `cd frontend && pnpm tsc --noEmit && pnpm vitest run --reporter=dot`
Expected: 通过

- [ ] **Step 6: 提交**

```bash
git add frontend/src/i18n
git commit -m "i18n(frontend): 新增 templates namespace"
```

---

## Task 9: 前端 `api.ts` 改造

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.test.ts`
- Modify: 所有 `API.createProject(...)` 调用点（仅 CreateProjectModal.tsx，Task 14 重写时处理签名）

- [ ] **Step 1: 写失败测试（`frontend/src/api.test.ts`）**

```ts
it("createProject sends object body with style_template_id and model fields", async () => {
  mockFetch({ success: true, name: "p1" });
  await API.createProject({
    title: "P1",
    style_template_id: "live_premium_drama",
    content_mode: "drama",
    aspect_ratio: "9:16",
    video_backend: "gemini/veo-3",
    default_duration: 8,
  });
  expect(lastFetchBody()).toEqual({
    title: "P1",
    style_template_id: "live_premium_drama",
    content_mode: "drama",
    aspect_ratio: "9:16",
    video_backend: "gemini/veo-3",
    default_duration: 8,
  });
});

// 删除之前的 `API.createProject(title, style, contentMode, ...)` 位置参数测试
// 删除 `deleteStyleImage` / `updateStyleDescription` 的测试
```

- [ ] **Step 2: 修改 `api.ts` 的 `createProject` 签名**

```ts
export interface CreateProjectPayload {
  title: string;
  name?: string;
  content_mode?: "narration" | "drama";
  aspect_ratio?: "9:16" | "16:9";
  generation_mode?: "single" | "grid";
  default_duration?: number | null;
  style_template_id?: string | null;
  video_backend?: string | null;
  image_backend?: string | null;
  text_backend_script?: string | null;
  text_backend_overview?: string | null;
  text_backend_style?: string | null;
}

static async createProject(
  payload: CreateProjectPayload,
): Promise<{ success: boolean; name: string; project: ProjectData }> {
  return this.request("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
```

- [ ] **Step 3: 删除 `deleteStyleImage` 和 `updateStyleDescription`**

删除两个静态方法的定义。

- [ ] **Step 4: 处理编译错误**

由于 `createProject` 签名变化，`CreateProjectModal.tsx` 会编译失败。暂时不 fix（Task 14 重写它）。如果需要保留构建绿，可以先在 CreateProjectModal 里手工改调用点（用对象形式），Task 14 重写时再删。实用做法：先把旧 modal 内的调用改成 `API.createProject({ title, content_mode: contentMode, ... })`。

- [ ] **Step 5: 跑前端测试**

Run: `cd frontend && pnpm vitest run --reporter=dot`
Expected: 通过

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api.ts frontend/src/api.test.ts frontend/src/components/pages/CreateProjectModal.tsx
git commit -m "feat(api): createProject 改为对象入参 + 支持模版与模型字段"
```

---

## Task 10: 前端 `ModelConfigSection` 共享组件

**Files:**
- Create: `frontend/src/components/shared/ModelConfigSection.tsx`
- Create: `frontend/src/components/shared/ModelConfigSection.test.tsx`

- [ ] **Step 1: 写失败测试 `ModelConfigSection.test.tsx`**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ModelConfigSection } from "./ModelConfigSection";

const PROVIDERS = [
  { id: "gemini", models: { "veo-3": { supported_durations: [4, 6, 8] } } },
  { id: "ark",    models: { "seedance": { supported_durations: [5, 8, 10] } } },
];

describe("ModelConfigSection", () => {
  it("displays 'use global default' option and current default hint", () => {
    render(
      <ModelConfigSection
        value={{
          videoBackend: "",
          imageBackend: "",
          textBackendScript: "",
          textBackendOverview: "",
          textBackendStyle: "",
          defaultDuration: null,
        }}
        onChange={() => {}}
        providers={PROVIDERS}
        globalDefaults={{
          video: "gemini/veo-3",
          image: "gemini/nano-banana",
          textScript: "gemini/g25",
          textOverview: "gemini/g25",
          textStyle: "gemini/g25",
        }}
      />
    );
    expect(screen.getAllByText(/当前全局默认/)).toHaveLength(5);
  });

  it("rerenders duration buttons when video model changes", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ModelConfigSection
        value={{ videoBackend: "gemini/veo-3", imageBackend: "", textBackendScript: "", textBackendOverview: "", textBackendStyle: "", defaultDuration: null }}
        onChange={onChange}
        providers={PROVIDERS}
        globalDefaults={{ video: "", image: "", textScript: "", textOverview: "", textStyle: "" }}
      />
    );
    expect(screen.getByRole("radio", { name: "4s" })).toBeInTheDocument();
    rerender(
      <ModelConfigSection
        value={{ videoBackend: "ark/seedance", imageBackend: "", textBackendScript: "", textBackendOverview: "", textBackendStyle: "", defaultDuration: null }}
        onChange={onChange}
        providers={PROVIDERS}
        globalDefaults={{ video: "", image: "", textScript: "", textOverview: "", textStyle: "" }}
      />
    );
    expect(screen.getByRole("radio", { name: "5s" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "4s" })).not.toBeInTheDocument();
  });

  it("resets duration to auto when new video model does not support current value", () => {
    const onChange = vi.fn();
    render(
      <ModelConfigSection
        value={{ videoBackend: "gemini/veo-3", imageBackend: "", textBackendScript: "", textBackendOverview: "", textBackendStyle: "", defaultDuration: 4 }}
        onChange={onChange}
        providers={PROVIDERS}
        globalDefaults={{ video: "", image: "", textScript: "", textOverview: "", textStyle: "" }}
      />
    );
    // 切 ark 模型
    fireEvent.change(screen.getByLabelText(/视频模型/), { target: { value: "ark/seedance" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ videoBackend: "ark/seedance", defaultDuration: null }));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && pnpm vitest run ModelConfigSection`
Expected: FAIL（组件不存在）

- [ ] **Step 3: 实现 `ModelConfigSection.tsx`**

```tsx
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { lookupSupportedDurations, DEFAULT_DURATIONS } from "@/utils/provider-models";
import type { ProviderInfo, CustomProviderInfo } from "@/types";

export interface ModelConfigValue {
  videoBackend: string;
  imageBackend: string;
  textBackendScript: string;
  textBackendOverview: string;
  textBackendStyle: string;
  defaultDuration: number | null;
}

export interface ModelConfigSectionProps {
  value: ModelConfigValue;
  onChange: (next: ModelConfigValue) => void;
  providers: ProviderInfo[];
  customProviders?: CustomProviderInfo[];
  globalDefaults: {
    video: string;
    image: string;
    textScript: string;
    textOverview: string;
    textStyle: string;
  };
  /** 可选：模型类别启用开关，默认全启 */
  enable?: { video?: boolean; image?: boolean; text?: boolean; duration?: boolean };
}

export function ModelConfigSection({ value, onChange, providers, customProviders = [], globalDefaults, enable = {} }: ModelConfigSectionProps) {
  const { t } = useTranslation("templates");
  const showVideo = enable.video !== false;
  const showImage = enable.image !== false;
  const showText = enable.text !== false;
  const showDuration = enable.duration !== false;

  const supportedDurations = useMemo(() => {
    if (!value.videoBackend) return DEFAULT_DURATIONS;
    return lookupSupportedDurations(providers, value.videoBackend, customProviders) ?? DEFAULT_DURATIONS;
  }, [providers, customProviders, value.videoBackend]);

  const handleVideoChange = (next: string) => {
    const nextDurations = next
      ? (lookupSupportedDurations(providers, next, customProviders) ?? DEFAULT_DURATIONS)
      : DEFAULT_DURATIONS;
    const resetDuration = value.defaultDuration !== null && !nextDurations.includes(value.defaultDuration);
    onChange({ ...value, videoBackend: next, defaultDuration: resetDuration ? null : value.defaultDuration });
  };

  // ... 渲染下拉 + duration 按钮组 + 当前全局默认提示
  // 单个 ModelRow(label, options, value, onSelect, defaultHint)
  return (
    <div className="space-y-3">
      {/* 省略完整 JSX — 参考 ProjectSettingsPage 同款结构 + "使用全局默认" option */}
    </div>
  );
}
```

（完整 JSX 照 ProjectSettingsPage 抽取；TDD 迭代直到测试 pass。）

- [ ] **Step 4: 跑测试**

Run: `cd frontend && pnpm vitest run ModelConfigSection`
Expected: 3 tests pass

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/shared/ModelConfigSection.tsx frontend/src/components/shared/ModelConfigSection.test.tsx
git commit -m "feat(shared): ModelConfigSection 共享模型配置组件"
```

---

## Task 11: 向导 Step1 `WizardStep1Basics`

**Files:**
- Create: `frontend/src/components/pages/create-project/WizardStep1Basics.tsx`
- Create: `frontend/src/components/pages/create-project/WizardStep1Basics.test.tsx`

- [ ] **Step 1: 写测试**

```tsx
// 测试：title 空时 onNext 禁用；点"下一步" emit onNext；radio 切换触发 onChange。
```

- [ ] **Step 2: 实现组件（受控 props：`value` / `onChange` / `onNext` / `onCancel`）**

包含：title 输入、content_mode radio、aspect_ratio radio、generation_mode radio、Cancel + 下一步按钮、title 校验。

- [ ] **Step 3: 跑测试 + 提交**

```bash
git add frontend/src/components/pages/create-project/WizardStep1Basics*.tsx
git commit -m "feat(wizard): Step1 基础信息子组件"
```

---

## Task 12: 向导 Step2 `WizardStep2Models`

**Files:**
- Create: `frontend/src/components/pages/create-project/WizardStep2Models.tsx`
- Create: `frontend/src/components/pages/create-project/WizardStep2Models.test.tsx`

- [ ] **Step 1: 测试 — 渲染描述文案 + 内嵌 ModelConfigSection + onBack/onNext**

- [ ] **Step 2: 实现**

```tsx
import { ModelConfigSection } from "@/components/shared/ModelConfigSection";
// ...
<div className="text-xs text-gray-500 mb-3">{t("default_hint")}</div>
<ModelConfigSection value={...} onChange={...} providers={...} globalDefaults={...} />
```
从 `API.getProviders()` + `API.listCustomProviders()` + `API.getSystemConfig()` 拉取；useEffect 首次挂载 fetch。

- [ ] **Step 3: 测试 + 提交**

```bash
git commit -m "feat(wizard): Step2 模型与时长子组件"
```

---

## Task 13: 向导 Step3 `WizardStep3Style`

**Files:**
- Create: `frontend/src/components/pages/create-project/WizardStep3Style.tsx`
- Create: `frontend/src/components/pages/create-project/WizardStep3Style.test.tsx`

- [ ] **Step 1: 测试**

覆盖：
- 默认 tab = `live`，默认选中 `live_premium_drama`
- 点击不同模版卡片 → onChange 传入新 id
- 切到 `custom` tab → onChange 清空 template_id、切 mode
- 切回 `live` tab → 恢复上一次选中
- `custom` 模式下未上传时，onCreate 被 disabled

- [ ] **Step 2: 实现**

```tsx
interface Props {
  value: {
    mode: "template" | "custom";
    templateId: string | null;
    activeCategory: "live" | "anim";
    uploadedFile: File | null;
    uploadedPreview: string | null;
  };
  onChange: (next) => void;
  onBack: () => void;
  onCreate: () => void;
  creating: boolean;
}
```
内部：
- TabBar（3 个按钮，切 tab 时 emit onChange 更新 mode/activeCategory）
- 模版网格：`getTemplatesByCategory(activeCategory)` → 5 列卡片；选中 indigo 边框 + 右上角 ✓
- 自定义上传：复用现有 upload UI（来自旧 CreateProjectModal 的 style 参考图部分）
- 底部：取消 / 上一步 / 创建项目

- [ ] **Step 3: 测试 + 提交**

```bash
git commit -m "feat(wizard): Step3 风格选择（tab + 模版网格 + 自定义上传）"
```

---

## Task 14: 重写 `CreateProjectModal` 容器

**Files:**
- Modify: `frontend/src/components/pages/CreateProjectModal.tsx`（完全重写）
- Modify: `frontend/src/components/pages/CreateProjectModal.test.tsx`（完全重写）

- [ ] **Step 1: 重写测试文件**

覆盖：
- 初始 step=1；title 填写后点"下一步"到 step=2
- step=2 点"下一步"到 step=3（无校验）
- step=3 默认选中 live_premium_drama，点"创建项目"调用 API.createProject({ style_template_id: "live_premium_drama", ... }) 并 navigate
- step=3 切到 custom + 上传文件 + 点创建 → 先 createProject（template_id=null）再 uploadStyleImage
- step=2 切回 step=1 保留字段
- 创建中按钮 disabled + spinner

- [ ] **Step 2: 实现容器**

```tsx
export function CreateProjectModal() {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [basics, setBasics] = useState({ title: "", contentMode: "narration", aspectRatio: "9:16", generationMode: "single" });
  const [models, setModels] = useState<ModelConfigValue>({ videoBackend: "", imageBackend: "", textBackendScript: "", textBackendOverview: "", textBackendStyle: "", defaultDuration: null });
  const [style, setStyle] = useState({ mode: "template", templateId: DEFAULT_TEMPLATE_ID, activeCategory: "live", uploadedFile: null, uploadedPreview: null });
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    setCreating(true);
    try {
      const resp = await API.createProject({
        title: basics.title.trim(),
        content_mode: basics.contentMode,
        aspect_ratio: basics.aspectRatio,
        generation_mode: basics.generationMode,
        default_duration: models.defaultDuration,
        style_template_id: style.mode === "template" ? style.templateId : null,
        video_backend: models.videoBackend || null,
        image_backend: models.imageBackend || null,
        text_backend_script: models.textBackendScript || null,
        text_backend_overview: models.textBackendOverview || null,
        text_backend_style: models.textBackendStyle || null,
      });
      if (style.mode === "custom" && style.uploadedFile) {
        try { await API.uploadStyleImage(resp.name, style.uploadedFile); }
        catch { /* warning toast */ }
      }
      setShowCreateModal(false);
      navigate(`/app/projects/${resp.name}`);
    } finally { setCreating(false); }
  };

  return (
    <Modal maxWidth={step === 3 ? "4xl" : step === 2 ? "xl" : "md"}>
      <StepIndicator current={step} />
      {step === 1 && <WizardStep1Basics value={basics} onChange={setBasics} onNext={() => setStep(2)} onCancel={close} />}
      {step === 2 && <WizardStep2Models value={models} onChange={setModels} onBack={() => setStep(1)} onNext={() => setStep(3)} onCancel={close} />}
      {step === 3 && <WizardStep3Style value={style} onChange={setStyle} onBack={() => setStep(2)} onCreate={handleCreate} creating={creating} onCancel={close} />}
    </Modal>
  );
}
```

- [ ] **Step 3: 跑测试**

Run: `cd frontend && pnpm vitest run CreateProjectModal`
Expected: 全部 pass

- [ ] **Step 4: 手工 UI 冒烟（dev 环境）**

```bash
# 后端
uv run python -m server &
# 前端
cd frontend && pnpm dev
```
操作：新建项目 → 走完三步 → 验证创建成功 + 生成首段分镜时看到 style 生效。
若 UI 有目测问题，在 Task 15 中一并调试。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/pages/CreateProjectModal.tsx frontend/src/components/pages/CreateProjectModal.test.tsx
git commit -m "feat(wizard): CreateProjectModal 重写为三步向导"
```

---

## Task 15: 概览页移除"项目风格"区块

**Files:**
- Modify: `frontend/src/components/canvas/OverviewCanvas.tsx`
- Modify: `frontend/src/components/canvas/OverviewCanvas.test.tsx`

- [ ] **Step 1: 定位风格区块**

Read OverviewCanvas.tsx，找到 `{t("project_style_title")}` 所在的 section。

- [ ] **Step 2: 删除整个 section**

删除：
- `styleImageFp`, `styleDescriptionDraft`, `deletingStyleImage`, `savingStyleDescription` 等 state
- `handleStyleImageChange` / `deleteStyleImage` / `saveStyleDescription` 等 handler
- 对 `API.uploadStyleImage` (若仅出现在该 section)、`API.deleteStyleImage`、`API.updateStyleDescription` 的调用
- JSX 中整个"项目风格"卡片

⚠️ **注意**：Step 3 Task 13 的自定义上传用的是 `API.uploadStyleImage`，OverviewCanvas 删除 import 后要确保仍被其他 file 使用。

- [ ] **Step 3: 更新测试**

删除测试文件中所有"project style section" 相关断言。

- [ ] **Step 4: 跑测试**

Run: `cd frontend && pnpm vitest run OverviewCanvas`
Expected: 通过

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/canvas/OverviewCanvas.tsx frontend/src/components/canvas/OverviewCanvas.test.tsx
git commit -m "refactor(overview): 移除项目风格区块（风格变为创建一次性设置）"
```

---

## Task 16: `ProjectSettingsPage` 接入 `ModelConfigSection`

**Files:**
- Modify: `frontend/src/components/pages/ProjectSettingsPage.tsx`

- [ ] **Step 1: 找到 model config 渲染块**

在 ProjectSettingsPage.tsx 中 `{t("video_model")}`、`{t("image_model")}` 周围块。

- [ ] **Step 2: 抽取成 `<ModelConfigSection />` 调用**

把原来的分散渲染替换为：
```tsx
<ModelConfigSection
  value={{ videoBackend, imageBackend, textBackendScript: textScript, textBackendOverview: textOverview, textBackendStyle: textStyle, defaultDuration }}
  onChange={(next) => {
    setVideoBackend(next.videoBackend);
    setImageBackend(next.imageBackend);
    setTextScript(next.textBackendScript);
    setTextOverview(next.textBackendOverview);
    setTextStyle(next.textBackendStyle);
    setDefaultDuration(next.defaultDuration);
  }}
  providers={providers}
  customProviders={customProviders}
  globalDefaults={{ video: globalDefaults.video, image: globalDefaults.image, textScript: globalDefaults.textScript, textOverview: globalDefaults.textOverview, textStyle: globalDefaults.textStyle }}
/>
```
（注意这里 `globalDefaults.textScript` 等字段如果以前不存在，去 `API.getSystemConfig` 返回里补上读取。）

- [ ] **Step 3: 删除旧的分散 JSX（video/image 单独块）**

- [ ] **Step 4: 跑测试**

Run: `cd frontend && pnpm vitest run ProjectSettings`
Expected: 通过

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/pages/ProjectSettingsPage.tsx
git commit -m "refactor(settings): ProjectSettingsPage 接入 ModelConfigSection"
```

---

## Task 17: 前端 i18n dashboard 清理

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`

- [ ] **Step 1: grep 未被引用的 key**

```bash
rg -l "project_style_title|style_image_preview|style_desc_saved|style_image_updated|style_image_deleted|confirm_delete_style_image|style_desc_textarea_placeholder" frontend/src
```
Expected: 只剩翻译文件自身。若有 code 使用则先解决。

- [ ] **Step 2: 从 dashboard.ts 删除这些 key（zh + en）**

保留 `upload_reference_image` 之类仍在 Step3 自定义 tab 使用的 key（视具体实现决定）。

- [ ] **Step 3: 跑 i18n 一致性测试**

Run: `uv run python -m pytest tests/test_i18n_consistency.py -v`
Expected: pass（zh/en 对齐）

- [ ] **Step 4: 提交**

```bash
git add frontend/src/i18n
git commit -m "i18n: 清理不再使用的 project_style_* / style_image_* key"
```

---

## Task 18: 缩略图资源入库 + 临时产物清理

**Files:**
- Add: `frontend/public/style-thumbnails/*.png`（已存在）
- Delete（本地）: `projects/style-thumbnails/`
- Delete（本地）: `.superpowers/brainstorm/style-thumbnails-*.json`、`generate-all-thumbnails.py`、`style-thumbnails-bootstrap.py`、`thumbnails-progress.log`

- [ ] **Step 1: 验证缩略图数量与体积**

```bash
ls frontend/public/style-thumbnails/*.png | wc -l   # 应为 36
du -sh frontend/public/style-thumbnails/            # 应约 8.6M
```

- [ ] **Step 2: 检查 .gitattributes / .gitignore 是否允许 PNG**

```bash
grep "\.png" .gitignore  # 若禁了 png，则调整（本任务前提是允许）
```

- [ ] **Step 3: 删除本地临时产物**

```bash
rm -rf projects/style-thumbnails/
rm -f .superpowers/brainstorm/style-thumbnails-*.json \
      .superpowers/brainstorm/generate-all-thumbnails.py \
      .superpowers/brainstorm/style-thumbnails-bootstrap.py \
      .superpowers/brainstorm/thumbnails-progress.log \
      .superpowers/brainstorm/batch*.{sh,py,json} 2>/dev/null
```

- [ ] **Step 4: 入库缩略图**

```bash
git add frontend/public/style-thumbnails/
git commit -m "chore(assets): 入库 36 张风格模版缩略图"
```

---

## Task 19: 端到端验证 + 收尾

**Files:**
- Final commit: 整理

- [ ] **Step 1: 后端全测试**

Run: `uv run python -m pytest --cov=lib --cov=server`
Expected: 全 pass，覆盖率不低于基线

- [ ] **Step 2: 前端全测试 + typecheck**

Run: `cd frontend && pnpm check && pnpm build`
Expected: 全 pass

- [ ] **Step 3: 手工冒烟**

场景 1（模版）：
- 启 dev → 点"新建项目" → step1 填标题、默认其他 → step2 全留空 → step3 默认 live_premium_drama → 创建
- 打开项目 → 触发一个分镜生成 → 查 `project.json` 确认 `style_template_id` + `style` 长文本

场景 2（自定义）：
- 新建项目 → step3 切"自定义" → 上传一张图 → 创建 → 打开项目 → 查 `style_image` + `style_description` 存在

场景 3（迁移）：
- 选一个旧值为 `Photographic` 的老项目，用 `git stash` 回退代码前先备份其 `project.json`
- 切到本分支后，打开该项目 → 再读 `project.json` 应有 `style_template_id: "live_premium_drama"`

场景 4（模型覆盖）：
- 新建项目 → step2 选非默认 video_backend → 创建 → `project.json` 应有 `video_backend` 字段
- 打开 ProjectSettingsPage → ModelConfigSection 显示相同选择

- [ ] **Step 4: final review 过 spec**

对照 `docs/superpowers/specs/2026-04-14-style-presets-design.md`：
- 所有 § 覆盖到了吗？
- 已知缺口（anim_ghibli 带字水印）是否还需处理？若 PR 里不处理，确认 README / issue 追踪

- [ ] **Step 5: PR 描述**

```bash
git log main..HEAD --oneline  # 查看所有 commit
gh pr create --title "feat: 新建项目向导 + 风格模版系统" --body-file <(cat <<'EOF'
## Summary
- `CreateProjectModal` 重构为三步向导（基础信息 → 模型 → 风格）
- 引入 36 条内置画风模版（18 真人 + 18 动画）替代旧 Photographic/Anime/3D Animation 三选一
- 风格参考图作为"自定义" tab，与模版互斥
- 旧项目懒迁移：`style ∈ {Photographic, Anime, 3D Animation}` → 新模版
- 概览页移除"项目风格"区块（风格变为创建一次性设置）
- 抽出 `ModelConfigSection` 共享组件，供向导 Step2 与 ProjectSettingsPage 复用
- CreateProjectRequest 新增 `style_template_id` + 5 个模型字段（留空=用全局默认）

## Test Plan
- [ ] `uv run python -m pytest` 全通过
- [ ] `cd frontend && pnpm check && pnpm build` 全通过
- [ ] 手工冒烟：模版 / 自定义 / 迁移 / 模型覆盖 四场景
EOF
)
```

---

## Task 20: ProjectSettingsPage 风格修改区块（Spec 漏项补丁）

原 spec 把"事后修改风格"放在 Out of scope，实际是漏项——概览页拿掉后用户失去修改入口。
本 PR 内补回。改动一并合入。

- **20a**：抽 `frontend/src/components/shared/StylePicker.tsx`（纯展示，3 tab + 网格 + 上传），重构 `WizardStep3Style` 内部用之；切 tab 时清对侧字段保证互斥
- **20b**：`UpdateProjectRequest` 加 `style_template_id` + `clear_style_image`；`update_project` 处理器展开 prompt + 清 style_image；`upload_style_image` 末尾清 style_template_id
- **20c**：`api.ts` updateProject 类型加 `clear_style_image`；`ProjectData` 加 `style_template_id`；i18n 加 4 个键
- **20d**：`ProjectSettingsPage` 在 ModelConfigSection 上方新增"项目风格"卡片；独立 `savingStyle` loading state；保存按钮互斥 + 必选其一逻辑；成功后 refetch
- **20e**：`tests/test_projects_router.py` 新增 3 条 update 路径测试；`tests/test_files_router.py` 扩 style-image 断言；新增 `ProjectSettingsPage.test.tsx` 4 条；spec L29 修订

## 非目标（明确不在本 PR）

- 缩略图质量优化（anim_ghibli 等带水印文字）：后续 issue
- 模版 prompt 升级传导到已创建项目：设计上不做
- 用户自建模版：未来迭代
