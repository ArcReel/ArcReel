# 参考生视频模式 实现 Roadmap（多 PR 拆分）

> **For agentic workers:** 本文件是 7 个 PR 的索引。每个 PR 指向独立的详细 plan 文件（`2026-04-17-reference-to-video-prN-*.md`），按依赖顺序逐个执行。REQUIRED SUB-SKILL：对每个独立 plan 使用 superpowers:subagent-driven-development 或 superpowers:executing-plans。

**Goal**：在 ArcReel 新增第三种生成模式「参考生视频」——直接用角色/场景/道具参考图多镜头生成视频，跳过分镜。覆盖四家供应商（Ark / Grok / Veo / Sora），并完成前端、后端、Agent 全链路。

**Architecture**：新增 `generation_mode` 项目级 / 集级字段；脚本形态新增 `content_mode=reference_video`（`video_units[]` + multi-shot prompt + `@` 提及）；后端新增 `/reference-videos` 路由族 + executor；前端新增 `ReferenceVideoCanvas` + `MentionPicker` + `GenerationModeSelector`；Agent 新增 `split-reference-video-units` subagent，并扩展 `generate-script` / `generate-video` / `manga-workflow` 按 mode 分支。

**Tech Stack**：Python 3.11+ / FastAPI / SQLAlchemy async ORM / Pydantic / pytest | React 19 + TypeScript / vitest / wouter / zustand / Tailwind / i18next

## 参考设计

- Spec: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md`
- Seedance 2.0 文档: `docs/ark-docs/seedance2.0.md`
- 前序依赖（main 已落地）：`docs/superpowers/specs/2026-04-15-global-asset-library-design.md`（clue→scene/prop 拆分 + 全局资产库）

## PR 拆分总览

| PR | 里程碑 | 范围 | 依赖 | 详细 plan | 估行数 |
|---|---|---|---|---|---|
| **PR1** | M1 SDK 验证 | `verify_reference_video_sdks.py` + 四家能力矩阵报告 | 无 | `2026-04-17-reference-to-video-pr1-sdk-verification.md` | ~500 |
| **PR2** | M2 数据模型 + parser | `ReferenceVideoScript` Pydantic、`shot_parser.py`、`data_validator` 扩展、`generation_mode` 字段 | 无 | `2026-04-17-reference-to-video-pr2-data-model.md` | ~800 |
| **PR3** | M3 后端 | `/reference-videos` 路由 + `reference_video_tasks.py` executor + queue/worker dispatch + 归档/费用 | PR2 | `2026-04-17-reference-to-video-pr3-backend.md`（待写） | ~1500 |
| **PR4** | M4 前端框架 | `GenerationModeSelector`（项目级 + 集级）、`EpisodeModeSwitcher`、`ReferenceVideoCanvas` 基础三栏布局、`TimelineCanvas` 按 mode 分支 | PR3 | `2026-04-17-reference-to-video-pr4-frontend-shell.md`（待写） | ~1200 |
| **PR5** | M4b 前端编辑器 | `MentionPicker`、`ReferenceVideoCard`（prompt 编辑器 + Shot/`@` 高亮 + 自动保存）、`ReferencePanel`（拖拽换序） | PR4 | `2026-04-17-reference-to-video-pr5-frontend-editor.md`（待写） | ~1500 |
| **PR6** | M5 Agent 工作流 | `split-reference-video-units` subagent、`generate-script` / `generate-video` / `manga-workflow` skill 扩展、`CLAUDE.md` 更新 | PR2 + PR3 | `2026-04-17-reference-to-video-pr6-agent-workflow.md`（待写） | ~800 |
| **PR7** | M6 E2E + 发版 | `test_reference_video_e2e.py`、i18n 一致性、覆盖率达标、生成模式切换保留策略、UI 文案补全 | PR1-6 全部 | `2026-04-17-reference-to-video-pr7-e2e-release.md`（待写） | ~600 |

**总估算**：~6900 行代码+测试；~7 周（每周 1 PR）。

## 依赖图

```
PR1 (SDK 验证)   ──┐
                    │                      ┌── PR6 (Agent)
PR2 (数据模型) ─────┼── PR3 (后端) ────────┤
                    │                      ├── PR4 (前端框架) ── PR5 (前端编辑器)
                    └──────────────────────┘                                 │
                                                                              │
                                   PR7 (E2E + 发版) ←───────────────────────┘
```

- PR1、PR2 无外部依赖，可并行开工。
- PR3 依赖 PR2 合并（路由需要 Pydantic 模型）。
- PR4、PR6 都依赖 PR3（前端调后端 API、Agent skill 调后端 API）。
- PR5 依赖 PR4（复用基础 Canvas）。
- PR7 等所有前序合并后做联调。

## 推荐实施顺序

**第 1 周**：PR1（SDK 验证）+ PR2（数据模型）并行  
**第 2 周**：PR3（后端）  
**第 3 周**：PR4（前端框架）+ PR6（Agent，并行）  
**第 4 周**：PR5（前端编辑器）  
**第 5 周**：PR7（E2E + 发版）

## 每个 PR 的验收门槛（通用）

- 所有新增 test 通过，覆盖率 ≥ 90%
- `uv run ruff check . && uv run ruff format .` 干净
- 前端 `pnpm check`（typecheck + test）通过
- 改动对旧项目零回归：`effective_mode()` 缺省回退 `storyboard`
- i18n key zh/en 成对添加（`test_i18n_consistency.py` 不报错）
- PR 描述里列出本 PR 覆盖的 spec 章节

---

## PR1：M1 SDK 验证

**目标**：产出四家供应商的参考生视频能力矩阵，决定 Sora 是否降级为单图、Grok 请求体是否需要强制压缩。

**Spec 覆盖**：§2.1（范围）、§8.1（验证脚本）、附录 B（能力矩阵）

**交付物**：
- `scripts/verify_reference_video_sdks.py` — CLI，接受 `--provider`/`--refs`/`--duration` 参数
- `docs/verification-reports/reference-video-sdks-2026-04-XX.md` — 验证报告（Markdown）
- `tests/scripts/test_verify_reference_video_sdks.py` — 对 CLI 的 argparse + 输出格式的单测

**详细 plan**：`2026-04-17-reference-to-video-pr1-sdk-verification.md`

**关键验证项**：
- Ark Seedance 2.0 / 2.0 fast：9 张 refs + multi-shot + `generate_audio`
- Grok grok-imagine-video：7 张 refs + multi-shot；记录请求体大小
- Gemini Veo：3 张 refs + 8s
- OpenAI Sora：多张 `input_reference` 或降级单图（**重点**）

---

## PR2：M2 数据模型 + parser

**目标**：落地 `ReferenceVideoScript` Pydantic 模型、`shot_parser`、`generation_mode` 项目字段、`data_validator` 扩展。不改任何路由或前端；是 PR3 的前置。

**Spec 覆盖**：§4（数据模型）、§4.1（project.json）、§4.2（Pydantic）、§4.3（parser）、§4.5（映射表）、§4.6（effective_mode）、§11（schema_version 策略）

**交付物**：
- `lib/script_models.py` — 新增 `Shot` / `ReferenceResource` / `ReferenceVideoUnit` / `ReferenceVideoScript`
- `lib/reference_video/__init__.py` + `lib/reference_video/shot_parser.py` — prompt ↔ Shot[]/references 的双向解析
- `lib/data_validator.py` — 接受 `content_mode=reference_video` 脚本
- `lib/project_manager.py` — `effective_mode(project, episode)` 辅助
- `tests/lib/test_script_models_reference.py`、`tests/lib/test_shot_parser.py`、`tests/lib/test_data_validator_reference.py`、`tests/lib/test_project_manager_effective_mode.py`

**详细 plan**：`2026-04-17-reference-to-video-pr2-data-model.md`

**验收**：
- `pytest tests/lib/test_script_models_reference.py tests/lib/test_shot_parser.py -v` 全绿
- `uv run python -c "from lib.script_models import ReferenceVideoScript; ReferenceVideoScript.model_json_schema()"` 不报错
- 不动 schema_version（v1 即可，`generation_mode` 缺省回退 storyboard）

---

## PR3：M3 后端（路由 + executor + queue）

**目标**：把 PR2 的数据模型接到可执行的服务端：CRUD 路由、queue/worker dispatch、executor 中 `@→[图N]` 渲染、参考图压缩、Veo/Sora 特判、归档目录。

**Spec 覆盖**：§5（后端）、§5.1-§5.4、§8.2（错误处理）、§8.3（i18n key）

**文件清单（新增 + 改造）**：

新增：
- `server/routers/reference_videos.py` — 6 个端点（list/add/patch/delete/reorder/generate）
- `server/services/reference_video_tasks.py` — `execute_reference_video_task`
- `tests/server/test_reference_videos_router.py`、`tests/server/test_reference_video_tasks.py`
- `tests/lib/test_image_compression_batch.py`

改造：
- `lib/generation_queue.py` — `task_type` 枚举加 `"reference_video"`，`media_type="video"` 共用并发通道
- `lib/generation_worker.py` — dispatch map 注册
- `lib/cost_calculator.py` — 按 unit × duration × 单价预估
- `lib/i18n/{zh,en}/errors.py` — 新 key：`ref_missing_asset` / `ref_duration_exceeded` / `ref_too_many_images` / `ref_payload_too_large` / `ref_sora_single_ref` / `ref_shot_parse_fallback`
- `server/services/project_archive.py` — 归档 `reference_videos/` 目录
- `server/app.py` — 挂载 `reference_videos.router`

**关键设计点**：
- **资产解析**：直接 `from lib.asset_types import BUCKET_KEY, SHEET_KEY`；避免硬编码
- **压缩**：`lib.image_utils.compress_image_bytes(long_edge=2048, q=85)`；`RequestPayloadTooLargeError` 触发二次压缩 `long_edge=1024, q=70`
- **NamedTemporaryFile**：压缩后路径由 executor 管理，`VideoBackend.generate` 调用完成后 try/finally 清理
- **Veo 特判**：`duration = min(duration, 8)`、`references = references[:3]`，超限由响应 `warnings[]` 回传前端
- **Sora 特判**：若 M1 验证结论为不支持多图，强制 `references = references[:1]` + warn

**详细 plan**：`2026-04-17-reference-to-video-pr3-backend.md`（待写，预计 ~20 task）

**验收**：
- `pytest tests/server/test_reference_videos_router.py tests/server/test_reference_video_tasks.py -v` 全绿
- 新错误 key 在 `test_i18n_consistency.py` 中成对出现
- queue lease 机制不被破坏（`media_type="video"` 共享通道）

---

## PR4：M4a 前端框架（模式选择器 + Canvas 基础）

**目标**：让用户能在 UI 里切换到参考模式并看到基础 Canvas（暂不含编辑能力，PR5 补齐）。

**Spec 覆盖**：§6.1（模式选择器）、§6.2（Canvas 三栏布局）、§6.4（StatusCalculator）

**文件清单**：

新增：
- `frontend/src/components/shared/GenerationModeSelector.tsx` — 三按钮 + 描述区（项目级 + 集级共用）
- `frontend/src/components/canvas/EpisodeModeSwitcher.tsx` — 集级分段控制
- `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx` — 三栏布局骨架（左 unit 列表 + 中占位 + 右预览）
- `frontend/src/types/reference-video.ts` — `ReferenceVideoUnit` / `Shot` / `ReferenceResource` 前端类型（与 PR2 Pydantic 对齐）
- `frontend/src/stores/reference-video-store.ts` — zustand store（list/selected/loading）
- `frontend/src/api/reference-videos.ts` — 对应 `/reference-videos/*` 的 fetch 封装
- 各自测试文件

改造：
- `frontend/src/components/pages/create-project/WizardStep2Models.tsx` — 嵌入 `GenerationModeSelector`
- `frontend/src/components/pages/ProjectSettingsPage.tsx` — 同上
- `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` — 按 `effective_mode` 切换到 `ReferenceVideoCanvas`
- `lib/status_calculator.py`（后端）/ `frontend/src/types/project.ts` — 加 `reference_video` 状态分支
- `frontend/src/i18n/{zh,en}/dashboard.ts`、`errors.ts` — 加参考模式文案

**关键设计点**：
- `GenerationModeSelector` 内部用内部枚举 `"storyboard" | "grid" | "reference_video"`；展示文案走 i18n key：`mode_storyboard` / `mode_grid` / `mode_reference_video`
- 状态计算：`progress = 已生成 units / 总 units`
- Canvas 先出三栏骨架 + mock 数据渲染；编辑器在 PR5 补

**详细 plan**：`2026-04-17-reference-to-video-pr4-frontend-shell.md`（待写，预计 ~15 task）

---

## PR5：M4b 前端编辑器（MentionPicker + prompt 编辑器 + references 面板）

**目标**：填满 `ReferenceVideoCanvas` 中栏的编辑能力——高亮、`@` 提及、自动保存、references 拖拽换序。

**Spec 覆盖**：§6.2（prompt 编辑器细节）、§6.3（MentionPicker）

**文件清单**：

新增：
- `frontend/src/components/canvas/reference/MentionPicker.tsx` — 三分组 combobox，键盘 ↑↓/Enter + 过滤
- `frontend/src/components/canvas/reference/ReferenceVideoCard.tsx` — prompt 编辑器 + 三色高亮 + `@` 提及接入 + debounce 自动保存
- `frontend/src/components/canvas/reference/ReferencePanel.tsx` — 右栏 references 缩略图 + 拖拽换序 + `+` 按钮
- `frontend/src/hooks/useShotPromptHighlight.ts` — Shot 段标 + `@` 提及的 tokenizer（前后端共用解析逻辑可借鉴 `lib/reference_video/shot_parser.py` 的 regex）
- 各自 `.test.tsx`

改造：
- `frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx` — 嵌入 card / panel / picker
- `frontend/src/stores/reference-video-store.ts` — 加 `updatePromptDebounced(unitId, prompt)` action

**关键设计点**：
- `MentionPicker` **数据源直接读 `assets-store.ts`**（main 已暴露 characters/scenes/props），不重复拉取
- 三色色板：`character-*`（现有）/`scene-*`/`prop-*`，与 `AssetSidebar` 分组一致
- prompt 保存后端会重算 `duration_seconds` + `references`，前端保存后要用响应更新本地状态（防止顺序错乱）
- 拖拽换序独立于 prompt 保存，走 `PATCH /units/{id}` 只带 `references` 字段

**详细 plan**：`2026-04-17-reference-to-video-pr5-frontend-editor.md`（待写，预计 ~18 task）

---

## PR6：M5 Agent 工作流

**目标**：让 Claude Agent 在 `generation_mode=reference_video` 时能端到端自动跑完项目——从小说原文到生成视频。

**Spec 覆盖**：§7（Agent 工作流）、§7.1-§7.5

**文件清单**：

新增：
- `agent_runtime_profile/.claude/agents/split-reference-video-units.md` — 新 subagent
- `agent_runtime_profile/.claude/references/generation-modes.md` — 三种生成模式完整路径（替代/升级 `content-modes.md`）

改造：
- `agent_runtime_profile/.claude/skills/generate-script/SKILL.md` — 加 `generation_mode == reference_video` 分支
- `agent_runtime_profile/.claude/skills/generate-script/scripts/generate_script.py` — schema 分派（`ReferenceVideoScript`）
- `agent_runtime_profile/.claude/skills/generate-video/SKILL.md` — 检测 `video_units` vs `segments`/`scenes`
- `agent_runtime_profile/.claude/skills/generate-video/scripts/generate_video.py` — 路由到 `/reference-videos/...` API
- `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` — Step 4/7/8 按 mode 分支
- `agent_runtime_profile/CLAUDE.md` — 补 `generation_mode` 概念、项目目录、技能表
- 确认 `generate-assets` skill（main 51dde36 已落地，支持 `--characters/--scenes/--props`）能被 `manga-workflow` 在参考模式前置阶段调用；无需新建 skill。

**关键设计点**：
- Subagent prompt 模板强调：shot 时长之和 ≤ 模型上限；references 来自 characters/scenes/props 三 bucket；描述用 `@名称` 不描外貌
- `generate-video` skill 统一入口：读 episode 脚本后检测顶层结构分派，保持与 storyboard/grid 的调用习惯一致

**详细 plan**：`2026-04-17-reference-to-video-pr6-agent-workflow.md`（待写，预计 ~12 task）

---

## PR7：M6 E2E + 发版

**目标**：跑通端到端真实场景，对齐 i18n/覆盖率，并处理 spec §11 留下的未决点。

**Spec 覆盖**：§9.2（集成测试）、§9.4（手动 SDK 验证）、§9.5（覆盖率）、§11（未决点）

**文件清单**：

新增：
- `tests/integration/test_reference_video_e2e.py` — fixture 项目 → 注册角色+场景+道具 → 混合 `@` 提及 → mock backend 回 mp4 → 校验落盘 + thumbnail + 元数据

改造：
- `server/services/generation_tasks.py`（若需）— 与 storyboard 对齐 `generate_audio` 默认值（推荐 `true`）
- `frontend/src/components/canvas/reference/EpisodeModeSwitcher.tsx` — 补"切换模式不删旧数据"的 UI 提示（`toast`/`modal`）
- `lib/i18n/{zh,en}/errors.py` — 查漏补缺 key
- 手动跑 `scripts/verify_reference_video_sdks.py`，更新 spec 附录 B 的能力矩阵

**关键决策**（本 PR 拍板）：
- `generate_audio` fallback 默认值
- `generation_mode` 切换是否清空旧数据（默认**不删**）
- 是否 bump `schema_version` v2（默认**不 bump**）
- Sora 参考模式是否完全隐藏（依据 M1 结论）

**详细 plan**：`2026-04-17-reference-to-video-pr7-e2e-release.md`（待写，预计 ~10 task）

---

## 风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Sora 多图能力与文档不符 | 中 | 能力矩阵缩水 | PR1 先验证；UI 降级为单图并警告 |
| Grok gRPC 请求体超限 | 中 | 生成失败 | 参考图压缩 + `RequestPayloadTooLargeError` 二次压缩重试 |
| 前后端 shot_parser 解析不一致 | 低 | 保存后 references 漂移 | 前后端都基于同一 regex 约定；PATCH 返回服务端权威结果回填 |
| 全局资产 import 冲突 | 低 | 参考图路径不存在 | PR3 executor 解析时在 MissingReferenceError 里明确提示 |
| Agent skill API 漂移 | 低 | 老项目跑不通 storyboard | PR6 仅消费 `generate-assets` 现有参数，不改其签名 |

## 里程碑追踪

- [ ] PR1 合并（SDK 验证报告产出）
- [ ] PR2 合并（数据模型 + parser）
- [ ] PR3 合并（后端可通过 curl 调 `/reference-videos/...`）
- [ ] PR4 合并（UI 能切到参考模式看到基础 Canvas）
- [ ] PR5 合并（能在前端编辑 prompt 并触发生成）
- [ ] PR6 合并（Agent 能端到端跑完）
- [ ] PR7 合并（E2E 绿 + 发版）
