# 参考图生视频模式（Reference-to-Video）设计文档

- **日期**：2026-04-15（2026-04-17 对齐 main 上的 clue→scene/prop 拆分与全局资产库重构）
- **分支**：`feature/seedance2-reference-to-video`
- **背景**：ArcReel 当前支持「图生视频」（逐张分镜 + 图生视频）与「宫格生视频」（宫格图切分 + 图生视频）两种生成模式。本 spec 新增第三种模式——**参考生视频**：跳过分镜生成，直接使用角色/场景/道具图作为参考图生成视频，单个视频可包含多个镜头（multi-shot）。

## 0. 术语表

本 spec 中「scene/场景」一词出现在三个层面，必须区分：

| 术语 | 含义 | 出处 |
|---|---|---|
| **场景资产**（scene asset） | `project.json.scenes` bucket 下的条目，带 `scene_sheet` 参考图 | `lib/asset_types.py` |
| **剧本分镜场景**（DramaScene） | 仅 drama 模式剧本中的一个分镜单元，字段名 `scenes: list[DramaScene]` | `lib/script_models.py` |
| **镜头**（Shot） | 参考模式下 video_unit 内的 multi-shot 子段，对应 Seedance `Shot N (Xs):` | 本 spec §4.2 |

"道具"对应 `project.json.props` bucket（`prop_sheet`）。"角色"对应 `characters` bucket（`character_sheet`）。全局资产库（`_global_assets/`，ORM `Asset` 表）跨项目复用三类资产，通过 import 同步到项目级 bucket。

## 1. 目标与动机

1. 接入 Ark Seedance 2.0 的多模态参考能力（最多 9 张参考图、4-15s、支持 generate_audio）。
2. 减少生成步骤：跳过分镜图生成环节，缩短出片路径。
3. 支持单视频多镜头，与 Seedance 2.0 文档中的 `Shot N (Xs):` prompt 约定对齐（参见 `docs/ark-docs/seedance2.0.md`）。
4. 同时引入参考图压缩通道，避免 Grok 等供应商的请求体/gRPC 尺寸限制。

## 2. 范围

### 2.1 In Scope

- **四家供应商全覆盖**：Ark Seedance 2.0 / 2.0 fast（首推）、Grok grok-imagine-video、Gemini Veo、OpenAI Sora。
- **项目级 + 集级**「生成模式」选择器（命名：**图生视频 / 宫格生视频 / 参考生视频**）。
- 独立 Episode 脚本数据模型（`content_mode` 新增第三种值 `reference_video`，使用 `video_units[]` 替代 `segments[]/scenes[]`）。
- multi-shot prompt + `@` 提及式参考图语法。
- 参考图上传/生成后调用前临时压缩（不落盘）。
- 新路由 / 服务 / 任务 executor；复用现有 GenerationQueue + Worker + VideoBackends + MediaGenerator + UsageTracker。
- Agent 工作流改造：新增预处理 subagent、扩展 `generate-script` / `generate-video` skill、扩展 `manga-workflow` 编排分支。
- SDK 真实验证脚本（`scripts/verify_reference_video_sdks.py`）产出四家能力矩阵。

### 2.2 Out of Scope（v1）

- 用户 ad-hoc 上传参考图（不进入 characters/clues 库）。
- 每个 shot 粒度的参考图绑定（v1 仅支持 unit 粒度的 reference 列表）。
- 集级拼接用 Veo extend 链接（沿用 ffmpeg concat）。
- 基于参考视频生成的「视频编辑 / 延长」二级能力（Seedance 2.0 edit/extend）。

## 3. 架构总览

```
前端                                后端                              Agent Runtime
────                                ────                              ─────────────
ReferenceVideoCanvas           →    POST /reference-videos/...    →   generate-script (扩展)
  ├─ UnitList                         ↓                                ├─ NarrationEpisodeScript
  ├─ MentionEditor (Shot N+@)        GenerationQueue                   ├─ DramaEpisodeScript
  └─ ReferencePanel                   ↓                                └─ ReferenceVideoScript (新)
                                     execute_reference_video_task
GenerationModeSelector (三选)        ↓                                generate-video (扩展)
  ├─ 项目级 (wizard/settings)        ├─ 解析 @ 提及 → references        └─ 检测 script 形状:
  └─ 集级 (timeline toolbar)         ├─ 内存压缩（compress_image_bytes） segments/video_units
                                     ├─ 渲染 prompt (@X → [图N])
                                     ├─ Veo/Sora 特判                 split-reference-video-units
                                     └─ VideoBackend.generate          (新预处理 subagent)
                                         (reference_images=...)
                                                                      manga-workflow (编排)
                                     ffmpeg concat (沿用)              └─ Step 4/7/8 按
                                         ↓                                generation_mode 分支
                                     projects/<p>/reference_videos/
```

## 4. 数据模型

### 4.1 `project.json` 扩展

```json
{
  "title": "...",
  "content_mode": "narration",
  "generation_mode": "reference_video",        // 新字段，三选一: storyboard | grid | reference_video
  "episodes": [
    {
      "episode": 1,
      "title": "江湖夜话",
      "script_file": "scripts/episode_1.json",
      "generation_mode": "reference_video"     // 可选，集级覆盖，默认继承项目级
    }
  ]
}
```

**`generation_mode` 与 `content_mode` 的关系**

- `project.generation_mode` 是意图字段，UI / Agent 据此决定生成哪种形态的脚本。
- `script.content_mode`（episode JSON 内字段）是脚本形态的 discriminator，三种取值：
  - `"narration"` — 配合 `generation_mode ∈ {storyboard, grid}`，脚本含 `segments[]`
  - `"drama"` — 配合 `generation_mode ∈ {storyboard, grid}`，脚本含 `scenes[]`
  - `"reference_video"` — 配合 `generation_mode == "reference_video"`，脚本含 `video_units[]`
- 当 `effective_mode == "reference_video"` 时，`project.content_mode` 字段被视为占位；脚本的 `content_mode` 强制为 `"reference_video"`。

### 4.2 `ReferenceVideoScript` Pydantic 模型（`lib/script_models.py`）

```python
class Shot(BaseModel):
    duration: int = Field(ge=1, le=15, description="该镜头时长（秒）")
    text: str = Field(description="镜头描述，可包含 @角色/@场景 引用")

class ReferenceResource(BaseModel):
    type: Literal["character", "scene", "prop"] = Field(description="引用的资源类型")
    name: str = Field(description="角色/场景/道具名称，必须在 project.json 对应 bucket 中已注册")

class ReferenceVideoUnit(BaseModel):
    unit_id: str = Field(description="格式 E{集}U{序号}")
    shots: list[Shot] = Field(min_length=1, description="1-4 个 shot")
    references: list[ReferenceResource] = Field(description="按顺序决定 [图N] 编号")
    duration_seconds: int = Field(description="派生字段：所有 shot 时长之和")
    duration_override: bool = Field(default=False, description="true 时停止自动派生")
    transition_to_next: Literal["cut", "fade", "dissolve"] = "cut"
    note: str | None = None
    generated_assets: GeneratedAssets = Field(default_factory=GeneratedAssets)

class ReferenceVideoScript(BaseModel):
    episode: int
    title: str
    content_mode: Literal["reference_video"]
    duration_seconds: int = 0
    summary: str
    novel: NovelInfo
    video_units: list[ReferenceVideoUnit]
```

### 4.3 Prompt 约定与派生规则

- 用户/Agent 维护的 prompt 文本形如：
  ```
  Shot 1 (3s): 中远景，主角推门进酒馆...
  Shot 2 (5s): 近景，对面的 @张三 抬眼...
  Shot 3 (3s): 中景，@主角 在 @酒馆 坐下...
  ```
- 前后端共用 parser（`lib/reference_video/shot_parser.py`）将 prompt 文本解析为 `Shot[]`，求和得 `duration_seconds`。
- `references` 的集合（并集）由 prompt 中出现过的 `@` 提及决定：新增 `@X` 即自动追加到 references，prompt 中所有 `@X` 删除后 X 自动从 references 移除。
- `references` 的**顺序**独立可调（UI 支持拖拽 / 重排），顺序决定 `[图N]` 编号；调整顺序不影响 prompt 文本。
- 调用 backend 时，prompt 里所有 `@X` 出现处均替换为对应 `[图N]` 后发给模型。
- 若 prompt 无 `Shot N (Xs):` 标记：视为单镜头，`duration_seconds` 采用 UI/API 手动输入值并置 `duration_override=true`。

### 4.4 资源路径约定

```
projects/<p>/
├── project.json                                 # schema_version ≥ 1；含 characters/scenes/props 三 bucket
├── characters/                                  # 角色参考图（character_sheet）
├── scenes/                                      # 场景参考图（scene_sheet）
├── props/                                       # 道具参考图（prop_sheet）
├── scripts/
│   └── episode_1.json                           # content_mode=reference_video, video_units=[...]
├── reference_videos/
│   ├── E1U1.mp4                                 # 视频文件
│   ├── E1U1_v2.mp4                              # 重生版本
│   └── thumbnails/
│       └── E1U1.jpg                             # 首帧
└── episodes/
    └── episode_1.mp4                            # ffmpeg concat 结果（沿用既有拼接逻辑）
```

（`_global_assets/` 位于 `projects/` 同级根目录，跨项目复用，通过 `/api/v1/assets` 路由 + `AssetRepository` ORM 表管理；参考模式仅读取项目级 bucket，由全局资产导入时同步。）

### 4.5 数据分层约束

- `references` 只存名称 + 类型；具体图片路径按类型从 `project.json` 对应 bucket 读时解析，直接复用 `lib/asset_types.py` 的 `BUCKET_KEY` / `SHEET_KEY` 映射：

  | type | bucket | sheet 字段 |
  |---|---|---|
  | `character` | `characters` | `character_sheet` |
  | `scene`     | `scenes`     | `scene_sheet` |
  | `prop`      | `props`      | `prop_sheet` |

- `duration_seconds` 是派生字段，保存前由 parser 自动计算；`duration_override=true` 时保留用户值。

### 4.6 effective_mode 解析

```
effective_mode(project, episode) =
  episode.get("generation_mode") or project.get("generation_mode") or "storyboard"
```

- 后端在入口（路由、任务 executor、Agent skill 前置检查）统一用此函数解析。
- 旧项目 / 缺字段视为 `"storyboard"`（兼容默认）。

## 5. 后端

### 5.1 路由 `server/routers/reference_videos.py`

挂载前缀 `/api/v1/projects/{project_name}/reference-videos`，鉴权 / i18n 注入与其他路由一致。

| 路径 | 方法 | 用途 |
|---|---|---|
| `/episodes/{ep}/units` | GET | 列出 video_units |
| `/episodes/{ep}/units` | POST | 新建 unit（手动） |
| `/episodes/{ep}/units/{unit_id}` | PATCH | 改 prompt / references / duration / transition / note |
| `/episodes/{ep}/units/{unit_id}` | DELETE | 删除 unit |
| `/episodes/{ep}/units/reorder` | POST | 拖拽换序 |
| `/episodes/{ep}/units/{unit_id}/generate` | POST | 入队生成，返回 task_id |

`PATCH` 接受 prompt 时在服务端运行 parser → 更新 `duration_seconds` 与 `references`。

### 5.2 服务 `server/services/reference_video_tasks.py`

新增 `execute_reference_video_task(project_name, resource_id, payload, *, user_id)`：

```
1. 加载 project / episode script / unit
2. 解析 references（按 lib.asset_types 映射表分派）：
   - type=character → project.characters[name].character_sheet
   - type=scene     → project.scenes[name].scene_sheet
   - type=prop      → project.props[name].prop_sheet
   - 缺图 → raise MissingReferenceError
3. 压缩参考图（内存 + NamedTemporaryFile）:
   - lib.image_utils.compress_image_bytes(long_edge=2048, q=85)
   - 失败回退 long_edge=1024, q=70
4. 渲染 prompt：
   - @角色/场景/道具 → [图N] 按 references 顺序替换
5. 解析 video provider / model（沿用 execute_video_task 解析链）
6. 模型特判：
   - Veo: duration_seconds = min(duration, 8), references = references[:3]
   - Sora: 若 SDK 验证结果显示不支持多图，references = references[:1]
   - 两者均通过 warning 字段回传前端
7. 构造 VideoGenerationRequest:
   - reference_images=<temp paths>
   - start_image=None
   - duration_seconds=unit.duration_seconds
   - generate_audio 按 project.video_model_settings.generate_audio
8. generator.generate_video_async(resource_type="reference_videos", resource_id=unit_id, ...)
9. 抽取首帧缩略图 → thumbnails/<unit_id>.jpg
10. 更新 generated_assets.video_clip/video_uri/status、emit project_change_event
11. 清理 NamedTemporaryFile
```

### 5.3 队列 / Worker

- `lib/generation_queue.py`：`task_type` 枚举新增 `"reference_video"`，`media_type="video"`（共用视频并发通道）。
- `lib/generation_worker.py`：dispatch map 注册 `"reference_video": execute_reference_video_task`。

### 5.4 版本管理 / 费用 / 导出

- **VersionManager**：复用 `resource_type="reference_videos"`，版本文件名 `E1U1_v{N}.mp4`。
- **cost_estimation**：`lib/cost_calculator.py` 增加按 unit 预估（unit_count × unit.duration_seconds × model_unit_price）。
- **project_archive**：导出 zip 时 `reference_videos/` 目录进入归档列表。
- **compose-video / jianying_draft**：若参考模式下需要导出剪映草稿，由对应 service 读取 `episodes/episode_N.mp4`（与其他模式一致）。

## 6. 前端

### 6.1 生成模式选择器（项目级 + 集级）

- **项目级**（新建项目向导 Step1 + 项目设置）：三按钮 + 描述区，按钮点击切换 description 文案。
- **集级**（`TimelineCanvas` 工具栏）：三段分段控制 `图生视频 / 宫格生视频 / 参考生视频`（B 方案）。
- 数据绑定：
  - 项目级写 `project.json.generation_mode`
  - 集级写 `project.json.episodes[i].generation_mode`（缺省继承）
- 模式命名沿用：**图生视频 / 宫格生视频 / 参考生视频**（与旧名称 storyboard/grid 映射内部保留枚举）。

### 6.2 `ReferenceVideoCanvas`

当 `episode.generation_mode == "reference_video"` 时替换原 `TimelineCanvas` 渲染内容。

- **三栏布局**：左（unit 列表）+ 中（prompt 编辑器 + references）+ 右（视频预览 + 版本元数据）。
- **unit 列表**：状态点（pending/running/ready）、unit_id、总时长、prompt 前两行预览、references pills。
- **prompt 编辑器**：
  - 高亮 `Shot N (Xs):` 段标
  - 按资产类型三色区分：`@张三`（角色色）/ `@酒馆`（场景色）/ `@长剑`（道具色）——与 `AssetSidebar` / `AssetLibraryPage` 的分组色板一致
  - 输入 `@` 弹 `MentionPicker`（combobox，按 character/scene/prop 三组分类、键盘 ↑↓ + Enter 选择、过滤匹配）
  - 自动保存 debounce；保存时后端重算 `duration_seconds`、`references`
- **references 面板**：按顺序显示 `[图1]...[图N]` 缩略图，可拖拽换序（触发编号重排），`+` 按钮打开 `MentionPicker` 等价 UI。
- **警告 chip**：解析失败、缺图、Veo 超限、references 数超限。
- **操作按钮**：生成 / 查看历史版本。

### 6.3 `MentionPicker`

独立组件：接受候选列表（characters + scenes + props，三分组带类型图标与预览图），返回 `{type, name}`。在 prompt 编辑器以及 references 面板中都复用。候选数据源直接复用 `frontend/src/stores/assets-store.ts` 已暴露的项目级资产集合，无需新建数据层。

### 6.4 其他改动

- **`AssetSidebar` 无需再改**：main 上 51dde36 已将其拆分为 characters/scenes/props 三组（并新增 `AssetLibraryPage` / `GalleryToolbar` / `CharactersPage` / `ScenesPage` / `PropsPage` 以及 `assets-store`）。参考模式直接消费这些既有结构。
- `StylePicker` 无影响。
- `StatusCalculator` 加 `reference_video` 状态分支：进度按已生成 units / 总 units 计算。
- i18n：zh/en 翻译 key 加参考模式相关文案（错误、提示、按钮）；需要兼容 `i18n/{zh,en}/assets.ts` 已存在的资产命名空间，避免 key 冲突。

## 7. Agent 工作流

### 7.1 编排层 `manga-workflow` 改造

按 `generation_mode` 分支：

```
Step 4 预处理
  if generation_mode == "reference_video":
    dispatch split-reference-video-units subagent
  elif content_mode == "narration":
    dispatch split-narration-segments subagent
  else:
    dispatch normalize-drama-script subagent

Step 5 JSON 剧本
  dispatch create-episode-script subagent  # 内部调用 generate-script skill
  generate-script 按如下矩阵选 Pydantic schema：
    effective_mode == "reference_video"          → ReferenceVideoScript
    effective_mode ∈ {storyboard, grid} && narration → NarrationEpisodeScript
    effective_mode ∈ {storyboard, grid} && drama    → DramaEpisodeScript

Step 6 角色 / 线索图：不变

Step 7 分镜 / 宫格
  if generation_mode == "reference_video":
    skip
  elif generation_mode == "grid":
    dispatch generate-assets (grid)
  else:
    dispatch generate-assets (storyboard)

Step 8 视频
  dispatch generate-assets (video)
  (generate-video 内部检测 script 是 video_units 还是 segments/scenes，路由到对应 API)
```

### 7.2 新增 subagent `split-reference-video-units`

- 输入：episode 号、小说原文路径、project.json 摘要
- 输出：`drafts/episode_N/step1_reference_units.md`
  - 按 video_unit 粒度拆分（1-4 shot/unit，每 shot 带估算时长）
  - 标注每个 unit 涉及的 characters / scenes / props（必须已在 project.json 对应 bucket 中注册）
- 遵循 subagent 职责边界：不修改代码、不决定模式；仅完成一个聚焦任务。

### 7.3 `generate-script` skill 扩展

- 新增分支：`generation_mode == "reference_video"` 时
  - 输入：`drafts/episode_N/step1_reference_units.md`
  - schema：`ReferenceVideoScript`
  - LLM 提示模板强调：
    1. 每 unit 1-4 shot，shot 时长之和不超过所选模型上限
    2. references 必须来自 project.json 已注册的角色 / 场景 / 道具（三类 bucket 任选）
    3. 描述里用 `@名称`，不描述外貌（外貌由参考图提供）
- 不在 SKILL.md 写模式自检；前置条件扩展为「三种预处理中间文件之一就绪」。

### 7.4 `generate-video` skill 扩展

- 脚本读 episode 脚本，检测顶层结构：
  - `video_units` 存在 → 调用 `/reference-videos/episodes/{ep}/units/{id}/generate`
  - `segments` / `scenes` 存在 → 调用 `/generate/video/{scene_id}`（原逻辑）
- SKILL.md 前置条件：episode 脚本存在 & 对应类型的资源已就绪（ref 模式要求引用到的 characters / scenes / props 三类 bucket 中的 sheet 图齐全）。
- `agent_runtime_profile/.claude/skills/generate-assets/` 在 main（51dde36）已存在，按 `--characters/--scenes/--props` 分派生成三类资产 sheet 图。参考模式无需新增 skill；前置条件直接调用该统一入口即可。

### 7.5 `agent_runtime_profile/CLAUDE.md` 更新

- 补充 `generation_mode` 概念与三种取值
- "工作流程概览" 更新 Step 4/7/8 的分支逻辑
- "项目目录结构" 新增 `reference_videos/` 目录
- "可用 Skills" 表注明 generate-script / generate-video 支持三种模式
- `references/content-modes.md` → `references/generation-modes.md`（或新增独立文档），完整列出 storyboard × narration/drama、grid × narration/drama、reference_video 三条路径。

## 8. SDK 验证与错误处理

### 8.1 验证脚本 `scripts/verify_reference_video_sdks.py`

用法：
```bash
python scripts/verify_reference_video_sdks.py --provider {ark|grok|veo|sora} --refs 3 --duration 8
```

每家验证项：

| 供应商 | 关键验证 | 期望 |
|---|---|---|
| Ark Seedance 2.0 | 9 张 + multi-shot + generate_audio | 成功 |
| Ark Seedance 2.0 fast | 同上 | 成功（记录耗时差异） |
| Grok grok-imagine-video | 7 张 + multi-shot | 成功；记录请求体大小，>8MB 时记录 gRPC 错误 |
| Gemini Veo | 3 张 + 8s | 成功 |
| OpenAI Sora | 多张 `input_reference` | **重点验证**：若仅支持单图则 v1 降级为单图 |

输出：`docs/verification-reports/reference-video-sdks-YYYY-MM-DD.md`（Markdown 报告，PR 附上）。

### 8.2 错误处理矩阵

| 错误类 | 触发 | 处理 |
|---|---|---|
| `MissingReferenceError` | @ 提及解析到不存在或无图的资源（character/scene/prop 任一 bucket 均覆盖） | 任务 fail，列出缺失项 + 类型；前端提示"先生成 X（角色/场景/道具）" |
| `DurationExceedsLimitError` | unit duration 超模型上限 | clamp + warn；前端先提示 |
| `TooManyReferencesError` | references 超模型上限 | clamp + warn；超出的 reference 在响应里返回 |
| `RequestPayloadTooLargeError` | gRPC / HTTP 请求体超限 | 二次压缩重试（long_edge=1024, q=70）；二次失败 → fail 并建议减少 refs |
| `ProviderUnsupportedFeatureError` | Sora 多图实测不支持 | 仅取首张 + warn |
| `ParseShotPromptError` | prompt 无 `Shot N (Xs):` 模式 | 视为单镜头，使用 unit.duration_seconds（`duration_override=true`） |

### 8.3 i18n key

所有错误消息都通过 `lib/i18n/{zh,en}/errors.py` 注入新 key。为避免 3 类资产重复 key，建议采用通用化命名 + 参数：

- `ref_missing_asset`（参数 `type` ∈ {character, scene, prop}、`name`）
- `ref_duration_exceeded`、`ref_too_many_images`、`ref_payload_too_large`
- `ref_sora_single_ref`、`ref_shot_parse_fallback`

`type` 参数的文案本地化复用已存在的 `lib/i18n/{zh,en}/assets.py` 命名空间（character/scene/prop 的显示名）。

## 9. 测试策略

### 9.1 单元测试

| 测试文件 | 覆盖点 |
|---|---|
| `tests/lib/test_script_models_reference.py` | `ReferenceVideoUnit` / `ReferenceVideoScript` 校验、`shots[].duration` 求和 |
| `tests/lib/test_shot_parser.py` | prompt → Shot[] / references 解析、`@` 替换为 `[图N]`、回退单镜头 |
| `tests/lib/video_backends/test_*_reference_mode.py` | 各 backend mock 调用，断言 `reference_images` 透传；Veo clamp；Sora 单图降级 |
| `tests/server/test_reference_videos_router.py` | CRUD / reorder / generate 全覆盖 |
| `tests/server/test_reference_video_tasks.py` | 缺图（三类 bucket 分别 miss）、压缩、@→[图N]、Veo/Sora 特判、payload-too-large 重试 |
| `tests/lib/test_image_compression_batch.py` | 批量压缩 9 张，内存峰值、输出尺寸断言 |
| `tests/agent/test_generate_script_reference_branch.py` | mock LLM，验证 ReferenceVideoScript schema 路径 |
| `tests/agent/test_generate_video_branch.py` | 检测 script 形状后路由到正确端点 |

### 9.2 集成测试

- `tests/integration/test_reference_video_e2e.py`：fixture 项目 → 注册角色 + 场景 + 道具（三 bucket 各一条）→ 注入三类 sheet 图 → 创建含 `@character`、`@scene`、`@prop` 混合提及的 prompt → 入队 → mock backend 回 mp4 → 校验文件落盘 + thumbnail + 元数据；shot_parser 正确解析为 3 个 references 且 `[图N]` 顺序稳定。

### 9.3 前端测试

- `ReferenceVideoCard.test.tsx`：Shot 标记高亮、@ 提及高亮、duration 自动派生。
- `MentionPicker.test.tsx`：键盘上下选择、回车插入、类型过滤。
- `GenerationModeSelector.test.tsx`：三选 + description 切换 + 项目级/集级独立状态。
- `TimelineCanvas.test.tsx`：按 generation_mode 切换到 `ReferenceVideoCanvas`。

### 9.4 手动 SDK 验证

- 非 CI 强制；开发时按需运行 `scripts/verify_reference_video_sdks.py`。
- PR 附上一次完整验证报告（至少 Ark + Grok + Veo；Sora 按实际情况）。

### 9.5 覆盖率

沿用项目 ≥80% 门槛；新模块预期 ≥90%。

## 10. 里程碑（草案）

| 阶段 | 交付 |
|---|---|
| M1 SDK 验证 | `verify_reference_video_sdks.py` + 报告；确认 Sora/Grok 实际能力 |
| M2 数据模型 + parser | `ReferenceVideoScript` Pydantic、shot_parser、单元测试 |
| M3 后端路由 + 任务 executor | 路由 / queue / worker / 任务；集成测试通过 |
| M4 前端画布 + 模式选择器 | ReferenceVideoCanvas、MentionPicker、Selector；组件测试 |
| M5 Agent 工作流 | split-reference-video-units subagent、generate-script / generate-video 扩展、manga-workflow 分支、CLAUDE.md 更新 |
| M6 联调 + 发版 | 端到端跑通、i18n 校验、覆盖率达标、合并 |

## 11. 未决 / 留给实施计划

- 预处理 subagent 的 LLM prompt 模板细节（v1 通过 M5 阶段设计）。
- Sora 真实能力验证结果决定是否完全隐藏 Sora 参考模式选项或降级为单图（M1 产出）。
- **切换集级 generation_mode 的处理策略**：从 `storyboard/grid` 切到 `reference_video` 时，是否清空旧 segments 并提示重跑预处理；反向切换是否保留 `video_units`。v1 默认：切换时仅修改字段，**不主动删除** 旧数据；Canvas 按 effective_mode 渲染对应视图；实施计划需给出 UI 提示文案。
- 视频生成 `generate_audio` 的默认值在 `project.video_model_settings` 不存在时的 fallback（推荐 `true`，但需要实施阶段与现有 storyboard 行为对齐确认）。
- **schema_version 升级策略**：main 当前 `CURRENT_SCHEMA_VERSION = 1`（v0→v1 拆 clues）。新增顶层 `generation_mode` + 新脚本形态 `content_mode == "reference_video"` + 新目录 `reference_videos/` 是否需要 bump 到 v2 并提供 `v1_to_v2` 迁移器？
  - 倾向：**不 bump**。`generation_mode` 缺省按 `effective_mode()` 回退 `"storyboard"`，`video_units` 仅在新模式下写入，对旧项目零影响。
  - 若决定 bump：实施阶段需新增 `lib/project_migrations/v1_to_v2_*.py`，内容仅限"补 `generation_mode: "storyboard"` 默认字段"。此为实施 M2/M3 阶段要拍板的点。

## 附录 A：关键文件改动清单

### 新增

```
lib/reference_video/
  __init__.py
  shot_parser.py                       # prompt ↔ Shot[]/references
lib/script_models.py                   # 新增 ReferenceVideoUnit/Script/Shot/ReferenceResource
server/routers/reference_videos.py
server/services/reference_video_tasks.py
frontend/src/components/canvas/reference/
  ReferenceVideoCanvas.tsx
  ReferenceVideoCard.tsx
  MentionPicker.tsx
  ReferencePanel.tsx
frontend/src/components/shared/GenerationModeSelector.tsx
frontend/src/components/canvas/EpisodeModeSwitcher.tsx
agent_runtime_profile/.claude/agents/split-reference-video-units.md
scripts/verify_reference_video_sdks.py
tests/...                              # 详见 §9
```

### 改动

```
lib/generation_queue.py                # task_type 新增
lib/generation_worker.py               # dispatch map
lib/cost_calculator.py                 # 按 unit 预估
lib/i18n/{zh,en}/errors.py             # 新 key
server/app.py                          # 挂载新路由
server/services/generation_tasks.py    # （无需改动；保留对比）
server/services/project_archive.py     # 归档新目录
frontend/src/components/canvas/timeline/TimelineCanvas.tsx    # 按 mode 切换
frontend/src/components/pages/create-project/WizardStep2Models.tsx # 加选择器
frontend/src/components/pages/ProjectSettingsPage.tsx         # 加选择器
agent_runtime_profile/CLAUDE.md
agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
agent_runtime_profile/.claude/skills/generate-script/{SKILL.md, scripts/generate_script.py}
agent_runtime_profile/.claude/skills/generate-video/{SKILL.md, scripts/generate_video.py}
agent_runtime_profile/.claude/references/content-modes.md  → generation-modes.md
```

### 不变

- VideoBackends（Ark/Grok/Gemini/OpenAI）已支持 `reference_images` 字段。
- MediaGenerator / VersionManager / UsageTracker 接口不变。
- ffmpeg concat 与剪映草稿导出。
- `lib/asset_types.py` 的 `BUCKET_KEY` / `SHEET_KEY` 映射——直接复用做资产类型分派。
- `server/routers/_bucket_router_factory.py`、`server/routers/{scenes,props,characters,assets}.py`——参考模式不改 bucket 路由；同构的 unit CRUD 可考虑复用该 factory 生成（可选优化）。
- `frontend/src/stores/assets-store.ts` / `frontend/src/components/layout/AssetSidebar.tsx` / `frontend/src/i18n/{zh,en}/assets.ts`——MentionPicker 直接复用。

## 附录 B：供应商能力矩阵

> PR7 Task 14 在 CI 环境尝试运行 `scripts/verify_reference_video_sdks.py`，四家因 API key 缺失未能完成真实调用（live validation pending）。以下数值取自 `lib/reference_video/limits.py`（single source of truth）+ 供应商文档。详见 `docs/verification-reports/reference-video-sdks-2026-04-20.md`。

| 供应商 | 最大参考图 | 最大时长 | multi-shot 可靠性 | generate_audio | 备注 |
|---|---|---|---|---|---|
| Ark Seedance 2.0 | 9 | 15s | 文档声明支持 | ✅ | 首推；live 验证 pending (PR7) |
| Ark Seedance 2.0 fast | 9 | 15s | 文档声明支持 | ✅ | 快模式；live 验证 pending (PR7) |
| Grok grok-imagine-video | 7 | 15s | 文档声明支持 | ✅（默认） | 请求体大小待 live 验证 |
| Gemini Veo | 3 | 8s | 受限 | ✅（Vertex） | executor 已 clamp；基于 SDK 文档 |
| OpenAI Sora | 1（当前 limits.py） | 12s | **待 live 验证** | - | spec §11 第 4 项决策依赖；未 live 验证前按单图降级 |
