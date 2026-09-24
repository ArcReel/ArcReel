# 时间线「批量生成」与分镜列表「添加」按钮写死禁用的原因

> 日期：2026-09-24。对应 [#2662](https://github.com/ArcReel/ArcReel/issues/2662)（地图 [#2653](https://github.com/ArcReel/ArcReel/issues/2653)）。
> 行号以 main `990ad5fc` 为准。只陈述事实，不给产品建议。
> 调查范围：`git blame` / `git log -S` / `git log -L`，PR #471 的逐提交内容与评审回复，PR #1891、#1893、#2535，issue #1749、#1740、#1908、#2510、#2174、#2663，ADR 0061 / 0073，`CONTEXT.md`，以及前后端对应代码。未跑代码，全部为静态阅读结论。

## 结论速览

| 按钮 | 位置 | 何时禁用 | 当时理由 | 现状 |
|---|---|---|---|---|
| 批量生成分镜图 | `frontend/src/components/canvas/timeline/TimelineCanvas.tsx:312-320`（`disabled` 在 315） | 首次出现即禁用：PR #471 首个提交 `bdef8a13a`，squash 为 `ff9ea3b9`（2026-05-07） | 源码注释「设计稿示例：批量按钮（暂不实现批量逻辑，仅占位入口）」 | 仍是占位。后端没有分镜图批量 REST 端点；Agent 工具 `generate_storyboards` 有批量能力 |
| 批量生成视频（时间线） | 同文件 `:321-329`（`disabled` 在 324） | 同上 | 同上 | 仍是占位。分镜路线的整批准入已接到 Agent 与工作流计划预览，但**没有 Web 提交端点**；PR #1891 明写「分镜路线的批量入口在 Web 侧仍是禁用占位」 |
| 批量生成视频（宫格） | `frontend/src/components/canvas/grid/GridImageToVideoCanvas.tsx:286-295`（`disabled` 在 289；票面写的 `timeline/` 路径不对，文件在 `grid/`） | PR #471 提交 `86a28a2df`：宫格画布从 TimelineCanvas 拆出时原样带过来 | 与时间线同源（复制的占位） | 同上（宫格属于分镜路线，不是参考路线） |
| 分镜列表「添加」 | `frontend/src/components/canvas/timeline/ShotList.tsx:194-203` | PR #471 第二轮评审修复 `6beec0937`（squash 进 `ff9ea3b9`） | CodeRabbit 指出这是死控件（没有 `onClick`），作者回复「"+ 新建" 死按钮先 `disabled + aria-disabled + title=add_episode_unavailable`，等接入再启用」；squash 提交信息写作「"+ 新建" 按钮先禁用（功能未接入）」 | 仍是占位。同一能力已由分镜详情里的「新增分镜」承担（#2510 / PR #2535），但只接到了 TimelineCanvas，没接宫格画布 |

总体：四个按钮都来自 v3 静态设计稿，实现时只做了外观、没做逻辑。禁用理由是「功能没接入」，找不到任何「因约束而禁用」的记录。之后各项批量能力陆续落地（整批准入、Agent 工具、参考路线的 Web 批量端点），但**分镜路线的 Web 批量提交端点一直没做**。Spec #1908 把它列为 Out of Scope（「分镜路线 Web 批量按钮（FU-26）」），之后没有单独立票。

## 1. 每个按钮何时、由谁禁用，当时的理由

### 1.1 来源：v3 设计稿

- PR #471「项目工作台全新 UI」（合并于 2026-05-07，squash 提交 `ff9ea3b9`）以设计稿 `docs/ArcReel Workspace v3.html` 为参考。设计稿在 PR 内由提交 `9a2ab205d` 加入、`4cea67679` 删除，从未进入 main。
- 设计稿（取自 `9a2ab205d`）里就有这几个按钮，全部是**没有 handler 的静态 `<button>`**：
  - 第 1401-1402 行：`<button className="sv-navbtn" …>{Icons.sparkle}<span>批量生成分镜图</span></button>`、`…<span>批量生成视频</span></button>`
  - 第 682 行：分镜列表（标题「分镜」）头部的 `{Icons.plus}<span>添加</span>`
- `git show ff9ea3b9^:…/TimelineCanvas.tsx` 中没有这两个按钮。它们是 #471 新增的，此前不存在一个「能用、后来被关掉」的版本。

### 1.2 时间线两个批量按钮

- PR #471 首个提交 `bdef8a13a`（`TimelineCanvas.tsx` 第 367-389 行）：

  ```tsx
  {activeTab === "timeline" && hasScript && !isGridMode && (
    <div className="mr-1 inline-flex items-center gap-1.5">
      {/* 设计稿示例：批量按钮（暂不实现批量逻辑，仅占位入口） */}
      <button type="button" className="sv-navbtn …" disabled title={t("batch_generate_storyboards")}>
  ```

  这段注释在 PR 后续提交中被删掉，没有进 squash。`git log -S "暂不实现批量逻辑"` 在 main 上查不到。
- CodeRabbit 评审 `3189574107`（TimelineCanvas.tsx:389）提出「批量按钮被禁用但无提示……建议添加 tooltip 说明功能即将推出」。作者在 PR #471 的「评审反馈处理小结」里把它归入「Push back」，理由是「TimelineCanvas 批量按钮无 tooltip（3189574107）— UX 风格偏好」。
- `git blame` 显示 `TimelineCanvas.tsx:314-316,323-325` 的 `disabled` 行都来自 `ff9ea3b9`，之后没人动过。`git log -S "batch_generate_storyboards"` 只命中 `ff9ea3b9`（#471）和 `a84fdcec`（#486，只补了越南语翻译）。

### 1.3 宫格画布的批量视频按钮

- PR #471 提交 `86a28a2df`「feat(grid): 宫格生视频工作台 v3（独立 canvas + 三 tab）」新建了 `grid/GridImageToVideoCanvas.tsx`，提交信息写明「复用 timeline 组件」「清理 TimelineCanvas 的 grid 分支死代码」。该提交里这个按钮（当时第 236-244 行）已经是 `disabled`，没有 `onClick`。
- 现行代码 `GridImageToVideoCanvas.tsx:286-295`，`git blame` 仍全部指向 `ff9ea3b9`。
- 同一画布的「生成全部宫格」按钮（`:268-280`）是可用的，调 `onGenerateGrid` → `POST /projects/{name}/generate/grid/{episode}`（`server/routers/grids.py:96`）。它生成的是宫格图，不是视频。

### 1.4 分镜列表「添加」按钮

- 设计稿里这个按钮没有 handler，#471 首个提交照搬时没有 `onClick`，也没有 `disabled`。
- CodeRabbit 评审 `3190069280`（ShotList.tsx:190）：「"新增"按钮当前是死控件……如果功能暂未接入，至少先降级成禁用态」。
- 作者在第二轮评审回复中写明：「**ShotList:187** — "+ 新建" 死按钮先 `disabled + aria-disabled + title=add_episode_unavailable`，等接入再启用」。对应提交 `6beec0937`「fix: 应对 PR #471 第二轮评审反馈」，squash 提交信息里写的是「ShotList: … "+ 新建" 按钮先禁用（功能未接入）」，同一轮新增了 i18n key `dashboard:add_episode_unavailable`。
- 文案 key 叫 `add_episode`（zh「添加」，`frontend/src/i18n/zh/dashboard.ts:1654`），tooltip 是 `add_episode_unavailable`（「暂未提供」，`:1655`）。这两个 key 与侧栏「添加剧集」按钮（`frontend/src/components/layout/AssetSidebar.tsx:255-266`，PR #777 `9e87972f` 起同样禁用）共用。按钮在分镜列表头部，设计稿里是分镜列表的「添加」，但代码里没有说明它要添加的是分镜还是剧集。
- `git log -L194,203:…/ShotList.tsx` 只有 `ff9ea3b9` 一条，按钮从未接上过任何动作。

## 2. 禁用之后，是否有别的入口承担同一能力

| 能力 | Web 入口 | Agent / MCP 入口 |
|---|---|---|
| 分镜图批量生成（分镜路线） | **无**批量端点。只有逐条的 `POST /projects/{p}/generate/storyboard/{segment_id}`（`server/routers/generate.py:188`）。工作流面板的「重生」只对 stale 产物逐条调用这个单条入口（见下） | `generate_storyboards` 工具（`server/media_tools/storyboards.py:250-276`）：不传 `segment_ids` 时只生成缺分镜图的项。它**不走**整批准入：逐目标 `builder.block(...)` 后跳过受阻目标，其余照常入队（`:156-189`） |
| 分镜视频批量生成（分镜路线，含宫格） | **无**批量提交端点。只有逐条的 `POST /projects/{p}/generate/video/{segment_id}`（`generate.py:252`）。整批准入只在工作流计划里以**无副作用预览**的形式出现（见下） | `generate_videos` 工具（`server/media_tools/videos.py:1542-1600`），scope 取 episode / scene / all / selected，走 `admit_storyboard_video_request`（`videos.py:394`）。非参考路线（含宫格）进分镜分支（`_resolve_reference_route`，`videos.py:418-433`）。ADR 0061 写的「Agent 的四个视频工具」已在 `b4724fed`「feat(media): unify video generation targets」（2026-08-25）合并为这一个工具 |
| 参考单元视频批量生成（参考路线） | **有**。`ReferenceVideoCanvas.tsx:986-991` 的「批量生成」按钮 → `enqueueReferenceVideoBatch`（`frontend/src/actions/generation.ts:359-380`）→ `POST …/reference-videos/episodes/{episode}/units/generate-batch`（`server/routers/reference_videos.py:735`），走 `admit_reference_video_batch`，结论弹窗是 `ReferenceBatchAdmissionDialog` | 同一 `generate_videos` 工具的参考分支 |
| 整集旁白配音 | 有。时间线与宫格画布的「批量生成旁白」按钮（`TimelineCanvas.tsx:330-339`、`GridImageToVideoCanvas.tsx:296-305`）→ `POST /projects/{p}/generate/tts`（`generate.py:527`） | — |
| 分镜手动新增 / 移除 | 时间线分镜详情里的「新增分镜 / 移除分镜」（`ShotStructureActions`，`frontend/src/components/canvas/timeline/ShotDetail.tsx:1377-1390`），由 #2510 / PR #2535 提交 `bec23208`（2026-09-17）加入。只有 `TimelineCanvas` 接了 `onInsertShot` / `onRemoveShot`（`StudioCanvasRouter.tsx:845-846`），`GridImageToVideoCanvas` 没接。ShotList 头部的「添加」按钮在这次改动中没有动 | Agent 的剧本批量编辑（insert_after / remove） |

### 工作流面板（PR #1893，提交 `830e95cd7`）

- 面板只陈述状态，动作交回既有入口：`StudioCanvasRouter.tsx:343-345` 的注释写明「重生复用本组件已有的入队回调。面板不自建播放器，也不自建入队路径」。
- 「重生」只对 **stale 产物**提供（`frontend/src/components/workflow/StaleArtifacts.tsx`，组件注释说明它「既不自动重生，也不把它算进缺口」）。`handleWorkflowRegenerate`（`StudioCanvasRouter.tsx:357-391`）逐个 unit 调用 `handleGenerateStoryboard` / `handleGenerateVideo`，也就是单条 REST 入口，不是批量入口，也不覆盖「缺失即生成」。
- 参考路线下面板不传 `onRegenerate`（`StudioCanvasRouter.tsx:760-768`），注释说明视频入队由 `ReferenceVideoCanvas` 自己的整批准入路径承担。
- 面板里的整批准入结论来自 `POST /projects/{name}/workflow-plan`（`server/routers/projects.py:802`）。`server/services/project/workflow_planner.py` 的模块 docstring 是「side-effect-free workflow planner」，分镜路线调用 `admit_storyboard_video_request`（`:339-362`）只做预览。「确认档位」按钮（`WorkflowStepRow.tsx:145-158`）只把 `confirmed_request_durations` 带进下一次计划求解（`lib/workflow/workflow_plan.py:30-45`，`WorkflowPlanRequest` 的说明是「Transient choices used to plan one request without changing project workflow state」），不会入队。

## 3. 解禁的前置条件与未解决约束

**没有找到「因约束而禁用」的记录。** 四个按钮都是设计稿占位，禁用理由统一是「功能未接入」（#471 源码注释与评审回复）。之后的相关记录如下：

- **PR #1891**（issue #1749，Spec #1740；合并于 2026-08-15，提交 `d76443e2`）落地整批准入时，在「已知未覆盖」里写明：「分镜路线的批量入口在 Web 侧仍是禁用占位，本次只把 Agent 侧接上共享准入」。
- **Spec #1908** 的 Out of Scope 列出「分镜路线 Web 批量按钮（FU-26）」。仓库里再搜不到 `FU-26`，没有找到对应的独立票。
- **#2663**（本地图下的兄弟票，被 #2662 阻塞）把「「批量生成」按钮是否放开及其形态」列为待定问题。

如果要接上，现行规则给出的约束（都来自已接受的 ADR / 术语表，不是本调研的建议）：

- **ADR 0061**（`docs/adr/0061-batch-video-admission-all-or-nothing.md`）：「生成全部 / 批量生成」的视频入口必须先整批准入，任一目标有问题就零任务。明确不采用「逐条准入、逐条入队（前端串行循环）」（第 13 行），所以不能在浏览器里循环调用单条视频端点来冒充批量。三种结局一律 HTTP 200 返回同一信封（第 15 行）。批量入口必须显式声明旁白交付方式，缺省或非法一律拒绝（第 21 行）；单条入口不受这条约束。判定走共享缝 `lib/generation/batch_admission.py` + `server/services/admission/video_batch_admission.py`，「Web 批量端点与 Agent 的四个视频工具都走这一条，不各自实现一份」（第 7 行）。`CONTEXT.md:142-144` 的「整批准入判定（batch_admission）」词条与此一致。
- **ADR 0073**（`docs/adr/0073-generation-entry-requires-registered-assets-with-sheets.md`）：生成入口（「单条生成与整批准入」）遇到引用未登记、或角色 / 场景 / 道具 / 衍生没有资产图，一律阻断。分镜图与视频的批量入口都属于生成入口。
- **ADR 0061 只管视频。** 分镜图批量没有对应 ADR；Agent 侧 `generate_storyboards` 现在的口径是逐目标阻断、其余照常入队（见第 2 节），不是全有或全无。
- ShotList「添加」：它要添加的对象（分镜还是剧集）在代码里不明确，见 1.4。「新增分镜」的现成路由与 store 动作已有（`bec23208`），宫格画布还没接（#2663 同样列出了「宫格画布接新增 / 删除、`ShotList` 添加按钮」）。

## 4. 后端对应的批量 REST 与整批准入是否就绪

| 项 | 状态 | 证据 |
|---|---|---|
| 整批准入纯折叠 | 就绪 | `lib/generation/batch_admission.py` |
| 分镜路线视频整批准入（状态适配） | 就绪 | `server/services/admission/video_batch_admission.py:651`（`admit_storyboard_video_batch`）、`:977`（`admit_storyboard_video_request`，含音频开关冲突合并）、`:857`（`build_storyboard_video_specs`）。调用方只有 Agent 工具（`server/media_tools/videos.py:394`）和工作流计划预览（`workflow_planner.py:362`） |
| 分镜路线视频批量 **REST 提交端点** | **不存在** | `server/routers/` 下批量相关的路由只有 `reference_videos.py:735`（参考路线）、`grids.py:96`（宫格图）、`generate.py:527`（整集 TTS）。`frontend/src/api.ts` 里也没有分镜视频批量方法（批量相关只有 `generateReferenceVideoBatch`:3052、`generateGrid`:2704、`generateEpisodeNarrationAudio`:1610） |
| 分镜图批量 REST 端点 | **不存在** | 同上。只有单条 `generate.py:188`。批量逻辑只在 Agent 工具 `server/media_tools/storyboards.py:102-247` |
| 参考路线视频批量 REST + 整批准入 | 就绪且已接 Web | `reference_videos.py:735-800`，`admit_reference_video_batch`（`video_batch_admission.py:449`），前端 `ReferenceVideoCanvas.tsx:589,986` |
| 远程 MCP | 已暴露 Agent 工具 | `server/remote_mcp.py:113` 包含 `generate_storyboards` / `generate_videos` |

补充：ADR 0061 第 9 行规定「入队中断不撤销已创建任务」。它由 PR #1922（`f37000db`）修订，与 PR #1891 描述里的「顺序入队中途失败撤销本次已创建的任务」不同，以 ADR 现行文本为准。
