# 新建项目向导 + 风格模版系统设计（2026-04-14）

## 背景

当前 `CreateProjectModal` 是一个单页小模态，`style` 固定为三选一（Photographic / Anime / 3D Animation），风格参考图作为可选上传框并与预设并存、生成时两者叠加进 prompt。产品侧希望：

1. 提供一套丰富的"画风模版"（参考 [docs/生图画风前置提示词4.10.docx](../../生图画风前置提示词4.10.docx)，共 36 条），每条带缩略图；
2. 风格参考图与模版互斥（只能选一种模式）；
3. 创建流程同时覆盖模型选择（因视频时长选项与 `video_backend.supported_durations` 联动，duration 必须排在模型选择之后）；
4. 与项目级"模型配置"的 ProjectSettingsPage 保持交互一致（共享组件）。

## 范围

**In scope**
- `CreateProjectModal` 重构为三步向导
- 36 条风格模版（18 真人 + 18 动画）+ 对应缩略图静态资源
- 风格参考图上传作为第三个 tab "自定义"（与模版互斥）
- 旧预设从 UI 移除；老项目懒迁移到新模版
- 概览页（`OverviewCanvas`）移除"项目风格"区块（style 变为创建一次性设置）
- 拆出 `ModelConfigSection` 共享组件，供向导 step 2 与 `ProjectSettingsPage` 共用
- `CreateProjectRequest` 新增模型字段与 `style_template_id`

**Out of scope**（未来可独立立项）
- 事后修改模版 / 在其他页面重新选模版
- 用户自建模版
- 模版导入 / 导出
- 缩略图端侧重生成（目前是静态资源）
- 概览页重构为新风格展示
- ProjectSettingsPage 在本 PR 仅做 ModelConfigSection 替换，不新增功能

## 数据模型

### `project.json` 字段

| 字段 | 旧 | 新 |
|---|---|---|
| `style` | `"Photographic"` \| `"Anime"` \| `"3D Animation"` | 完整画风 prompt 文本，如 `"画风：真人电视剧风格，精品短剧画风，大师级构图"` |
| `style_template_id` | — | 新增，`"live_premium_drama"` 等 36 选一，或 `null`（使用自定义参考图 / 未设） |
| `style_image` | 同现 | 不变；仅在 `style_template_id === null` 且用户上传参考图时有意义 |
| `style_description` | 同现 | 不变；AI 分析结果 |
| `video_backend` / `image_backend` | 已存在（ProjectSettingsPage 使用） | 不变，创建流程首次写入 |
| `text_backend_script` / `text_backend_overview` / `text_backend_style` | 已存在 | 同上 |
| `default_duration` | 已存在 | 不变；步骤 2 与 video_backend 联动 |

### 互斥约束

- 选择模版（step 3 "AI 漫剧" 或 "AI 真人剧" tab）：`style_template_id = <id>`, `style = <展开的 prompt 文本>`, `style_image/style_description` 清空（若有）
- 上传参考图（step 3 "自定义" tab）：`style_template_id = null`, `style = ""`, `style_image/style_description` 由 `POST /style-image` 写入
- 后端读取时若同时出现两者（历史数据竞态），以 `style_image` 优先（迁移逻辑主动清理 `style_template_id`）

### 模版注册表（权威源在后端）

`lib/style_templates.py`：
```python
STYLE_TEMPLATES: dict[str, dict] = {
    "live_premium_drama": {
        "category": "live",    # "live" | "anim"
        "prompt": "画风：真人电视剧风格，精品短剧画风，大师级构图",
    },
    # ... 共 36 条
}

LEGACY_STYLE_MAP = {
    "Photographic": "live_premium_drama",
    "Anime": "anim_kyoto",
    "3D Animation": "anim_3d_cg",
}

def resolve_template_prompt(template_id: str) -> str:
    """查表取 prompt，未知 id 抛 ValueError。"""
```

前端副本 `frontend/src/data/style-templates.ts` 仅保存 `{id, category, thumbnail}`（不存 prompt 文本，由后端在创建时查表展开）。

## 后端改动

### 新增文件
- `lib/style_templates.py` — 权威注册表 + 迁移映射
- `lib/i18n/{zh,en}/templates.py` — 模版名 / 标语的翻译（36 × 2 = 72 键 × 2 语言）

### 修改

**`lib/prompt_utils.py`**
- 删除 `STYLES` 常量与 `validate_style()`（旧三值不再是合法列表）
- `image_prompt_to_yaml()` 逻辑不变（`Style:` 字段直接塞 `style` 文本）

**`lib/project_manager.py`**
- `load_project(name)` 末尾调用 `_migrate_legacy_style(data)`：
  - 若 `style ∈ LEGACY_STYLE_MAP` 且 `style_template_id` 未设：
    - 若已有 `style_image`：`style_template_id = None`，`style = ""`（保留参考图）
    - 否则：`style_template_id = LEGACY_STYLE_MAP[style]`，`style = resolve_template_prompt(...)`
  - 持久化（`save_project`），触发 `project_change_source("migration")`
- `create_project_metadata(...)` 新增 `style_template_id: str | None = None`，`video_backend/image_backend/text_backend_*` 等参数一并转发；空值不写入

**`server/routers/projects.py`**
- `CreateProjectRequest` 新增：
  ```py
  style_template_id: str | None = None
  video_backend: str | None = None
  image_backend: str | None = None
  text_backend_script: str | None = None
  text_backend_overview: str | None = None
  text_backend_style: str | None = None
  ```
- 处理逻辑：
  - `style_template_id` 非空 → 调 `resolve_template_prompt` 展开到 `style`，验证合法，失败返回 400
  - `style_template_id` 为空 → 不写 template_id，`style = ""`
  - 其余模型字段空值不写入 `project.json`

**`server/routers/files.py`**
- 删除 `DELETE /projects/{name}/style-image`（无前端调用）
- 删除 `PATCH /projects/{name}/style-description`（无前端调用）
- 保留 `POST /projects/{name}/style-image`（自定义 tab 使用）

**`lib/prompt_builders.py`**
- `build_style_prompt_part` 不动；依赖数据层保证互斥，`style` 与 `style_description` 不会同时非空

## 前端改动

### 新增

- `frontend/src/data/style-templates.ts` — 36 条 `{id, category, thumbnail}` 清单
- `frontend/src/i18n/{zh,en}/templates.ts` — name / tagline / 分类名翻译
- `frontend/src/components/common/ModelConfigSection.tsx` — 抽出的共享模型配置块（video + image + 3 × text + duration）
- `frontend/public/style-thumbnails/*.png` — 36 张方形缩略图（~8.6 MB）

### 重构

**`CreateProjectModal.tsx`** 重写为三步向导：

```
CreateProjectModal
├─ StepIndicator        (1 基础 — 2 模型 — 3 风格)
├─ Step1Basics          max-w-md
│  └─ title / content_mode / aspect_ratio / generation_mode
├─ Step2Models          max-w-xl
│  ├─ 描述："默认模型可在 项目大厅 → 设置 → 模型选择 中调整"
│  └─ <ModelConfigSection />
│      - VideoModelSelect（允许"使用全局默认"，显示当前默认）
│      - DurationSelector（按 video_backend 动态）
│      - ImageModelSelect（显示当前默认）
│      - TextModelSection（script / overview / style 各一，均显示当前默认）
└─ Step3Style           max-w-xl → max-w-4xl（tab 切换时动画）
   ├─ TabBar (自定义 | AI 漫剧 | AI 真人剧)
   ├─ TemplateGrid (5 列 × N，active tab = live/anim 时显示)
   └─ CustomUpload (active tab = custom 时显示)
```

**状态管理**（本地 useState）：
```ts
type Step = 1 | 2 | 3;
type StyleMode = "template" | "custom";
interface State {
  step: Step;
  title: string;
  contentMode: "narration" | "drama";
  aspectRatio: "9:16" | "16:9";
  generationMode: "single" | "grid";

  videoBackend: string;        // "" 表示用全局默认
  imageBackend: string;
  textBackendScript: string;
  textBackendOverview: string;
  textBackendStyle: string;
  defaultDuration: number | null;

  styleMode: StyleMode;        // 当前激活 tab
  activeCategoryTab: "live" | "anim";  // 模版模式下当前 tab
  styleTemplateId: string | null;  // 默认 "live_premium_drama"
  uploadedFile: File | null;
  uploadedPreview: string | null;
}
```

**导航按钮行为**
- Step 1 下一步：`title.trim()` 非空
- Step 2 下一步：总是允许
- Step 3 创建项目：
  - `styleMode === "template"`：`styleTemplateId !== null`（默认已有）
  - `styleMode === "custom"`：`uploadedFile !== null`

**Tab 切换语义**
- 切到 `custom`：`styleMode = "custom"`；`styleTemplateId` 保留在 state 但不起作用（UI 视觉置灰）
- 切回 `live`/`anim`：`styleMode = "template"`；`activeCategoryTab = 切到的类别`；如果 `styleTemplateId` 不属于当前类别，重新选该类别第一项
- 两种模式的 state 在本地保留，切换无损失

**提交流程**
```ts
const projectResp = await API.createProject({
  title, style_template_id, content_mode, aspect_ratio, generation_mode,
  video_backend, image_backend,
  text_backend_script, text_backend_overview, text_backend_style,
  default_duration,
});

if (styleMode === "custom" && uploadedFile) {
  await API.uploadStyleImage(projectResp.name, uploadedFile);
}
navigate(`/app/projects/${projectResp.name}`);
```

### `OverviewCanvas.tsx` 删除"项目风格"区块

- 移除 `styleImageFp`, `styleDescriptionDraft`, 所有相关 handler 和 JSX
- 移除 OverviewCanvas 对 `API.deleteStyleImage` / `API.updateStyleDescription` 的引用；`API.uploadStyleImage` 保留（Step 3 自定义 tab 继续调用）
- 同步移除 `frontend/src/api.ts` 中的 `deleteStyleImage` / `updateStyleDescription` 函数定义以及对应 `api.test.ts` 用例
- 相关翻译 key（`project_style_title` / `style_image_preview` / `style_description` / `style_desc_saved` / `style_image_updated` / `style_image_deleted` / `confirm_delete_style_image` / `style_desc_textarea_placeholder` / `usage_guide` 等仅供风格区使用者）从 `dashboard.ts` 清理——grep 确认无其他引用者再删，否则保留

### `ProjectSettingsPage.tsx` 接入共享组件

- 将"模型配置 + 时长"部分抽取为 `<ModelConfigSection />`（`video_backend` / `image_backend` / 3 × `text_backend` / `default_duration`）原地替换
- `aspect_ratio` / `generation_mode` 继续内联在 ProjectSettingsPage（两处各有上下文语义，不纳入 ModelConfigSection）
- 向导 step 2 与该页共享同一套 ModelConfigSection，含"显示全局默认提示"

## 资源

- `frontend/public/style-thumbnails/*.png` — 36 张 1:1 PNG，已用 `generate-all-thumbnails.py` 调本地 generate_character 端点生成完成
- 已知问题：部分缩略图带水印文字（如 `anim_ghibli.png` 左上角有"「anim_ghibli」的全身立绘"），源于后端 `build_character_prompt` 的 character-sheet 模板；可接受作为第一版占位，plan 中单列一步用干净 prompt 重生
- 临时生成产物：`projects/style-thumbnails/`（项目目录）、`.superpowers/brainstorm/style-thumbnails-*`、`.superpowers/brainstorm/generate-all-thumbnails.py` —— 落盘后需清理（plan 收尾步骤）

## i18n

- 新 namespace：后端 `templates`、前端 `templates`
- 翻译 key：
  - `templates.category.{custom,live,anim}` — 3 个 tab 名
  - `templates.name.{id}` — 36 条模版名
  - `templates.tagline.{id}` — 36 条标语
  - `templates.default_hint` — "默认模型可在 项目大厅 → 设置 → 模型选择 中调整"（step 2 描述文案）
  - `templates.current_global_default` — "当前全局默认：{value}"
- 中文文案来自 docx；英文首版可直译（CI 的 `test_i18n_consistency.py` 自动 flag 缺失）

## 旧预设迁移

### 触发点
懒迁移：`ProjectManager.load_project()` 每次读取时检测，命中 LEGACY_STYLE_MAP 则就地重写并持久化

### 三种场景

| 旧值 | `style_image` 状态 | 迁移后 |
|---|---|---|
| `Photographic` | 无 | `style_template_id="live_premium_drama"`, `style=<live_premium_drama 的 prompt>` |
| `Anime` | 无 | `style_template_id="anim_kyoto"`, `style=<anim_kyoto 的 prompt>` |
| `3D Animation` | 无 | `style_template_id="anim_3d_cg"`, `style=<anim_3d_cg 的 prompt>` |
| `Photographic` / `Anime` / `3D Animation` | 已有参考图 | `style_template_id=None`, `style=""`（保留参考图，互斥化） |
| 其他字符串（自由文本 / 已迁移项目） | 任意 | 不动 |

### 副作用
- `updated_at` 会被刷新（一次性，下次读取已有 `style_template_id` 就不再触发）
- 若运行时 ID 未命中 `STYLE_TEMPLATES`（比如代码回滚但数据仍新），保留 `style` 原文本、不中断读取

## 测试计划

| 层 | 用例 |
|---|---|
| `lib/style_templates` 单测 | ID 全局唯一 / category 分类完整 / LEGACY_STYLE_MAP 三个目标 id 都存在 / `resolve_template_prompt` 未知 id 抛异常 |
| `lib/project_manager` 单测 | `_migrate_legacy_style` 三场景 + 有 style_image 优先 + 未知值不变 + 幂等（二次调用不变更） |
| `server/routers/projects` 路由测试 | CreateProjectRequest 新字段透传 / 模型字段空值不写入 / style_template_id 非法返回 400 / style_template_id 合法时 style 被写入展开文本 |
| 前端单测 `CreateProjectModal.test.tsx` | 三步导航（Next/Back） / Step 1 title 校验 / Step 2 duration 随 video_backend 变化 / Step 3 tab 切换清理互斥 state / 默认 tab + 默认卡片 / custom 未上传时 create 按钮 disabled |
| 前端单测 `ProjectSettingsPage.test.tsx` | 共享 `ModelConfigSection` 替换后无回归 |
| i18n CI | `test_i18n_consistency.py` 覆盖新 `templates` namespace（zh/en） |
| 手工验证 | Dev 启动后：创建项目走完三步 / 选择不同模版 / 上传参考图 / 切换 tab / 用旧 Photographic 项目触发懒迁移 |

## 回滚安全

- 数据模型加法为主。`style` 语义从短标签变长 prompt 是破坏性，但对生成链路透明（`Style:` 长字符串一样能喂 LLM）
- 缩略图总体积 ~8.6 MB，若超 repo 体积线后续压 WebP（plan 可选步骤）
- 迁移不可逆（会改写 project.json），但产物可读可用；若需更保险，可先 dry-run 打日志看命中数
- 删除的两个 endpoint（DELETE style-image / PATCH style-description）grep 确认前端无调用，外部无暴露

## 已知缺口 / 非目标

1. **缩略图质量**：部分带水印文字，本 PR 接受；plan 中"重生优化"单列步骤，用更干净的 portrait-only prompt
2. **事后改模版**：概览页被移除后，唯一入口是新建项目；用户若想换风格需重建项目或手工改 project.json（本次 PR 不解决）
3. **自定义模版**：用户无法保存自己的 prompt 为可复用模版（未来迭代）
4. **模版 prompt 升级不传导**：已创建项目的 `style` 是展开文本快照；后续若 registry 中的 prompt 改了，老项目不跟随（设计目标，见 § 3）

## 落地顺序（为 writing-plans 提示）

1. `lib/style_templates.py` + 单测
2. `lib/project_manager` 迁移 + 单测
3. `lib/prompt_utils` 清理
4. `CreateProjectRequest` + 路由
5. `server/routers/files.py` 清理两个端点
6. 前端 `style-templates.ts` + i18n
7. 前端 `ModelConfigSection` 抽取
8. `CreateProjectModal` 向导重写 + 测试
9. `OverviewCanvas` 移除风格区 + 测试
10. `ProjectSettingsPage` 接入共享组件 + 测试
11. 缩略图资源入库 + 临时产物清理
12. 手工 e2e + commit
