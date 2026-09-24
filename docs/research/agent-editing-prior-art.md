# 「Agent 剪辑」开源与商业实现调研（OpenMontage 等）

> 本文回答 #2669，隶属地图 #2667「Agent 自动剪辑」。调研对象是现有开源项目和商业产品怎么实现"Agent 剪辑"，以及 ArcReel 能借鉴什么。
>
> 只陈述一手事实：仓库源码、README 和官方文档。借鉴清单对照地图已定的方向来写：剪辑时间线是唯一真相源；L1 剪辑决策加 L2 看素材剪；通过内嵌 Agent 对话式介入；精修交给剪映。
>
> 调研日期 2026-09-24。开源仓库的链接尽量固定到当日默认分支的 commit；商业产品只看公开文档。标注"未核实"的结论可能已经过时。

## 一、核心结论（先给答案）

1. **剪辑决策的数据形态收敛到两种。**
   - **声明式剪辑单**：多数项目这样做。OpenMontage 的 `edit_decisions`、Shotstack 的 Edit JSON、editly 的 JSON5、NarratoAI 的片段数组，以及 Director 调用的 VideoDB `Timeline→Track→Clip`，都属于这一类。
   - **代码即时间线**：Remotion 的 TSX，以及 Diffusion Studio 新编辑器的 JSX。
   - 声明式剪辑单的核心字段高度一致：
     - 按轨道或层区分主画面、叠加、旁白、BGM、音效、字幕；
     - 每个 clip 带素材引用、入出点（source in/out）、在时间线上的位置或时长、转场，以及音量和 ducking。
   - OpenMontage 在每个 cut 上还有一个 `reason` 字段，用来记录决策理由。
2. **LLM 写时间线有三种方式：**
   - **整份重写**：OpenMontage、Shotstack MCP、Director、NarratoAI、FunClip 的首版都这样生成。
   - **代码补丁**：Remotion 官方模板用 `old_string/new_string`，Diffusion Studio 直接改 JSX 文件。
   - **操作式工具**：DaVinci Resolve MCP 有 `ripple_insert/move_clips/delete_clips/execute_swap` 等；剪映系 MCP 有 `add_video/add_audio/add_text`。

   面向**人对话式修改**、并且有安全闸门的，只有操作式工具这一派。代表是 Resolve MCP，它提供 dry-run、`confirm_token`、改前归档版本，并把结果输出为新的 variant 时间线。
3. **"看素材"有三种做法：**
   - **抽帧 → VLM → 带时间戳的文字描述**，缓存下来供 LLM 当文本读。Descript、NarratoAI 纪录片模式、VideoDB 索引属于这类。
   - **渲染带时间码的联系表或单帧图，直接交给多模态 Agent 看**。Diffusion Studio 的 `capture`、Resolve MCP 的 `timeline_frame.capture`、OpenMontage 的 `frame_sampler` 加宿主 Agent 读 PNG、Remotion 的 `still` 都是这种。
   - **视频原生模型**：Twelve Labs Pegasus/Marengo，Jockey、FunClip、NarratoAI 可选接入。

   **把看素材做成剪辑闭环的很少**，即挑版本、裁坏帧、触发重生成。
   - OpenMontage 做了成片后的自检（ffprobe、抽 4 帧查黑帧、检查音频）。
   - Diffusion Studio 做了 `capture` 加 `check` 之后再 `export`。
   - Resolve MCP 的 `rank_takes` 只评估口播流畅度，README 明确写着它**不负责**判断"哪条 take 最好"。
   - 没有任何一个实现会在看素材后**自动**重生成素材。
4. **人工介入普遍用三类机制：**
   - **只读看板加对话审批**：OpenMontage Backlot 与门控检查点。
   - **每轮 Agent 修改可回滚**：Descript 的 checkpoint 加 Revert；Resolve MCP 改动前先归档版本。
   - **先给人预览，再渲染**：Shotstack MCP 默认调用 `studio`，只有明确要求或没有人参与时才直接 `render_video`。

   这三类机制和 ArcReel 的方向（只读预览，加对话介入）直接对应。可编辑时间线 UI 这一派（Remotion Studio、Diffusion Studio、NarratoAI 表格）不在本图范围内。
5. **渲染后端普遍用 ffmpeg，Remotion 许可证是主要风险。**
   - Remotion 用自有许可证，不是开源许可证。员工超过 3 人的营利组织，只要做"视频编辑器、prompt-to-video、自动化流水线"，就算 Automators 档，按 $0.01/次渲染计费，每月最低 $100。使用 `<Player>` 也算在内。
   - Shotstack 是云端 SaaS，素材必须能通过公网 HTTPS 访问。
   - 因此"成片与剪映草稿从同一份剪辑时间线渲染"，最稳的组合是**自研 ffmpeg 渲染器**加 **pyJianYingDraft 导出器**。已有先例：NarratoAI 的同一份剪辑脚本既走 ffmpeg/MoviePy 渲染，也能导出剪映草稿；OpenMontage 的同一份 `edit_decisions` 按 `render_runtime` 路由到 Remotion、HyperFrames 或 FFmpeg 三种后端。

## 二、逐项目发现

### 1. calesthio/OpenMontage（重点）

- **定位**：自称"首个开源 agentic 视频制作系统"，约 6.1 万 star，AGPL-3.0，最后 push 为 2026-09-06（commit `08e2151f`）。
  - 它**没有代码编排器**，由宿主编程 Agent 做编排，宿主可以是 Claude Code、Cursor、Codex 等。
  - 它提供三样东西：YAML 流水线清单、Markdown 阶段 skill，以及 Python 工具库。
  - 来源：[README「How It Works」](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/README.md#how-it-works)
- **剪辑决策数据模型**：`edit_decisions` artifact，用 JSON Schema 校验，必填字段为 `version`、`cuts`、`render_runtime`。来源：[schemas/artifacts/edit_decisions.schema.json](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/schemas/artifacts/edit_decisions.schema.json)
  - `cuts[]` 的字段：
    - 基本字段：`id`、`source`（文件路径或 asset-manifest ID）、`in_seconds`、`out_seconds`、`speed`；
    - 层与变换：`layer`（取值 primary/overlay/background）、`transform{scale, position, animation, crop}`；
    - 转场：`transition_in`、`transition_out`、`transition_duration`；
    - `reason`，记录这一刀的决策理由。
  - cut 没有显式的时间线起点，按数组顺序首尾相接。
  - `overlays[]`：`asset_id`、`start_seconds`、`end_seconds`、`position`、`opacity`。
  - `audio`：
    - `narration.segments[]`，每段有 `asset_id` 和 `start/end_seconds`；
    - `music`，含 `volume`、`fade_in/out_seconds`、`ducking`；
    - `sfx[]`。
  - `subtitles`：`style`（sentence、word-by-word、karaoke）、`font`、`color`、`position`、`max_words_per_line`。
  - `renderer_family`、`render_runtime`（remotion/hyperframes/ffmpeg）、`composition_mode` 在提案阶段锁定，剪辑阶段"MUST carry forward unchanged"。
  - 剪辑单里还带着 `slideshow_risk_score` 和自由格式的 `metadata`。纪录片 skill 要求把重排理由、多样性替换等记录到 `metadata.reorder_notes` 和 `metadata.diversity_swaps`。
- **Agent 工具面**：
  - 剪辑阶段由宿主 LLM **整份编写** `edit_decisions` JSON，没有插入、裁切之类的操作式工具。
    - `cinematic.yaml` 的 edit 阶段写的是 `tools_available: []`，产出物是 `edit_decisions`，成功标准为 "Schema-valid edit_decisions artifact / cover the full planned runtime"。来源：[pipeline_defs/cinematic.yaml#L209-L227](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/pipeline_defs/cinematic.yaml#L209-L227)
  - "怎么剪"写在 Markdown skill 里。纪录片剪辑 skill 规定了以下内容：
    - 按基调设定节奏网格（最短、基准、最长停留时长），总时长误差控制在 ±10%；
    - 在素材里找最好的子窗口，并在两端各留 4～6 帧余量；
    - 卡音乐重拍，转场只用 cut、dissolve、fade 等少数几种；
    - 检查相邻镜头的多样性，使用 L-cut；
    - 遇到"重大改动"（加旁白、换音乐、时长大幅拉长）必须停下来问用户。
    - 来源：[skills/pipelines/documentary-montage/edit-director.md](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/skills/pipelines/documentary-montage/edit-director.md)
  - Python 工具通过 `from tools... import X; X().execute({...})` 调用，并通过注册表发现。来源：[AGENT_GUIDE.md](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/AGENT_GUIDE.md)
  - Agent 通过读取检查点 JSON 来"看见"时间线，检查点位于各阶段的 `checkpoint_<stage>.json`。
- **渲染后端**：`video_compose` 以整份 `edit_decisions` 为输入，按 `render_runtime` 路由。
  - Remotion：React 组件位于 `remotion-composer/`；
  - HyperFrames：HTML、CSS、GSAP；
  - FFmpeg：只用于简单的 concat 和 trim。
  - 如果运行时被悄悄换掉，会被标记为治理违规。
  - 来源：[tools/video/video_compose.py](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/video/video_compose.py)
- **看素材与审阅闭环**：
  - 素材理解工具在 `tools/analysis/`，包括以下几个：
    - `frame_sampler`：用 ffmpeg 按间隔、数量或时间戳抽帧；
    - `video_understand`：本地 CLIP、BLIP-2、LLaVA-1.5，用于描述、问答或分类；
    - `visual_qa`：抽帧，并做字幕遮挡和转场检查，"Returns frame paths so the agent can visually inspect them"；
    - `scene_detect`、`transcriber`。
    - 来源：[tools/analysis/visual_qa.py](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/analysis/visual_qa.py)、[video_understand.py](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/analysis/video_understand.py)
  - 真正的"看"由**宿主多模态 LLM 读取抽出来的 PNG** 完成。explainer 合成 skill 规定渲染后必须依次执行以下步骤：
    - ffprobe 检查（音频流必须存在）；
    - 在每个 cut 的中点抽帧；
    - 用 Whisper 转写成片音频，并与脚本比对；
    - 逐帧目检背景、字幕和叠加层；
    - 汇总后呈现给用户。
    - 来源：[skills/pipelines/explainer/compose-director.md#L300-L360](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/skills/pipelines/explainer/compose-director.md#L300-L360)
  - 运行时内置了成片自检：抽 4 帧检测黑帧，检查音频是否静音或削波；自检不通过就不向用户呈现。来源：[video_compose.py 约 L2389](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/video/video_compose.py#L2389)、[README「Quality Gates」](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/README.md#quality-gates)
  - 渲染前还有两道闸门：
    - 预合成校验：检查交付承诺，例如"动态为主"的项目不能 80% 是静图；
    - 6 维"幻灯片风险"评分：[lib/slideshow_risk.py](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/slideshow_risk.py)
  - 每个阶段最多修订 3 次：`max_revisions_per_stage: 3`。
  - **剪辑阶段本身不看素材**，因为 `tools_available` 为空。挑选素材版本和重生成发生在 assets 阶段，由人在分镜联系表上审批。
- **人工介入**：
  - proposal、script、scene_plan、assets、publish 这几个阶段默认 `human_approval_default: true`；edit 和 compose 默认为 false。
  - `write_checkpoint` 遇到"未记录审批就标记完成"的门控阶段，会直接抛出 `GATE VIOLATION`。被替换的检查点会归档保留。来源：[lib/checkpoint.py#L422-L512](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py#L422-L512)
  - **Backlot** 是一个"read-only local board"，内容完全从检查点文件推导：
    - 展示阶段、剧本、分镜卡片，以及素材的版本、提示词、单价和质量分；
    - 审批在聊天中完成；
    - 支持回放整次制作过程。
    - 来源：[backlot/README.md](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/README.md)
  - 预算控制：先预估，再预留、对账，单次动作超过阈值（默认 $0.50）需要确认。
- **许可证**：AGPL-3.0，与 ArcReel 相同，法律上兼容。
  - 但它不是可以直接引用的库：编排逻辑写在 skill 文本里，工具与自身的目录结构和检查点协议强耦合。
  - 定位上**只宜借鉴**：schema 字段、skill 里的剪辑手法、门控和自检机制。

### 2. Descript Underlord（商业，只看公开文档）

- **数据模型**：
  - 以脚本（转写文本）为主轴，改文字就是剪辑。
  - Scene 是带独立版式和图层的片段，项目下可以有多个 composition。
  - 内部时间线 JSON 没有公开：未核实。
  - 来源：[Scenes overview](https://help.descript.com/hc/en-us/articles/10248939749517-Scenes-overview)
- **Agent 工具面**：
  - 应用内是聊天式 co-editor。它知道用户当前选中了什么、在看哪个 composition，也可以指定场景、图层或段落作为上下文。
  - 能力覆盖字幕、切短视频、改画幅、转场、配音、音量平衡、B-roll、去口头禅、粗剪。
  - 对外 API 把能力收拢成**一个 agent 端点**：`POST /jobs/agent {project_id, composition_id, model, prompt, callback_url}`。官方原则是 "One agent API > 20 individual endpoints"。另有官方 MCP。
  - 内部工具名没有公开：未核实。
  - 来源：[Underlord 帮助页](https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript)、[Descript API](https://docs.descriptapi.com/)、[官方博客](https://www.descript.com/blog/article/dont-ship-your-api-as-an-mcp)
- **看素材**：
  - 抽帧后交给多模态模型生成描述，拼成"a big file, which you can literally just read"，也就是按时间戳索引的视觉描述文本。
  - 帧率和分辨率按内容类型调整：录屏用高分辨率以便看清文字，口播从轻处理。
  - 用途是按画面内容找片段、按脚本放置 B-roll。
  - 来源：[Underlord can watch videos](https://www.descript.com/blog/article/underlord-ai-can-watch-videos)
- **人工介入**：
  - 每次动手修改前建立 checkpoint，每条回复下都有 **Revert**。刷新页面后改用版本历史回滚。
  - 官方承认它会 "overpromise, make incorrect assumptions"。
  - 来源：[Revert or rollback changes](https://help.descript.com/hc/en-us/articles/36958274409357-Revert-or-rollback-changes-made-by-Underlord-beta)
- **渲染与许可**：闭源自有渲染。按 AI credits 计费，推理和工具执行分别计费；套餐见 [pricing](https://www.descript.com/pricing)。只能借鉴交互设计。

### 3. Diffusion Studio editor（MPL-2.0）

- **定位**：
  - 2026 年发布的新仓库 [diffusionstudio/editor](https://github.com/diffusionstudio/editor)，定位是"为 agent 打造的剪辑器"，采用 Electron 桌面壳，2026-09-23 仍在更新。
  - 旧的 `@diffusionstudio/core` 引擎仓库现在只剩文档和 playground。
- **数据模型**：**时间线就是 SolidJS 的 JSX 源码**，官方说法是 "the source is the document"。
  - 结构是 `<stage>` 下挂 `<scene>`，再下面是 `<video>`、`<audio>`、`<text>`、`<sequence>` 等元素。
  - 时间字段：`start`、`end`（在父时间线上的位置），`sourceIn`、`sourceOut`（素材截取范围），`playbackRate`，`syncTo`（按音频对齐）。
  - 每个元素都有 `id`，界面上的编辑按 id 写回代码。
  - 来源：editor 仓库 `docs/reference/jsx/timing.md`、README
- **Agent 工具面**：
  - 本机提供 MCP（`127.0.0.1:3274/mcp`），同时有 CLI `dapi`。
  - **没有"写时间线"的工具**，Agent 直接改 JSX 文件，应用会重新编译。
  - 工具有：`context`、`capture`、`check`、`export`、`media_probe`、`media_grab`、`media_filmstrip`、`media_waveform`、`media_transcribe`、`media_listen` 等。
  - 图片类结果最多 4 张、每张不超过 1MB 时，直接内联进 MCP 响应。
  - 内置聊天通过 `@anthropic-ai/claude-agent-sdk` 驱动本机的 Claude Code 或 Codex。
  - 来源：`docs/reference/tools/README.md`
- **看素材与闭环**（最完整）：
  - `capture` 渲染出与导出结果一致的帧，拼成带时间码的联系表，最大 2576×1456，官方注明这是"vision model 全细节读取的上限"。
  - `check` 检查黑帧空隙、永远不可见的节点、加载失败的素材源。
  - `media_grab`、`media_filmstrip` 看原始素材，`media_waveform` 标出静音段。
  - 官方建议的闭环：先 `capture` 加 `check` 验证，确认无误再 `export`。
  - 早期实验仓库 diffusionstudio/agent（MIT）已经是"先 `sample()` 抽帧、由视觉模型审核，通过后才 `render()`"。
  - 来源：`docs/reference/tools/capture.md`
- **渲染**：浏览器或本地的 WebCodecs 加 Mediabunny，默认输出 1080p H.264 + AAC。
- **人工介入**：双向编辑。画布拖动、时间线修剪都会作为 prop 写回 JSX；代码改动也会实时重绘画布。
- **许可**：MPL-2.0，属于文件级 copyleft，品牌资产除外。它是 TypeScript 桌面应用，**只宜借鉴**设计：id 写回、联系表、`check` 这类确定性体检。

### 4. DaVinci Resolve MCP（samuelgursky/davinci-resolve-mcp，MIT）

- **定位**：约 3.1k star，2026-09-24 仍在更新（v4.8.20）。通过 Resolve 官方 Scripting API 让 LLM 操作专业剪辑软件的时间线。
- **数据模型**：
  - 时间线数据在 Resolve 自己的项目库里，MCP 不另建模型。
  - 对 LLM 暴露的序列化形式是 `timeline.get_items` 返回的 `[{name, id, start, end, duration, kind}]`。
  - 帧号默认是时间线帧；标明 SOURCE 的动作使用素材帧。
  - 来源：[src/server.py](https://github.com/samuelgursky/davinci-resolve-mcp/blob/main/src/server.py)（约 L2775、L25447）
- **Agent 工具面**：**操作式工具**的代表。有 compound 模式（37 个工具，形如 `tool(action, params)`）和 granular 模式（389 个工具）。
  - `timeline`：`get_items`、`move_clips`、`delete_clips(ripple)`、`ripple_insert(..., dry_run, confirm_token)`、`create_variant_from_ranges`；
  - `timeline_item_takes`；
  - `edit_engine`：`plan_selects/execute_selects`、`plan_tighten/execute_tighten`、`plan_silence_ripple`、`plan_swap/execute_swap`、`rank_takes`；
  - 每次返回都带 `_operation` 信封，包含 status、verification、changes、execution_id。
  - 来源：[README](https://github.com/samuelgursky/davinci-resolve-mcp/blob/main/README.md)
- **看素材**：
  - `timeline_frame.capture` 把渲染后的帧（已含调色和字幕）作为 MCP 图像返回，用于改前改后对比。
  - `media_analysis` 的 `host_chat_paths` 协议：服务端抽帧后只返回路径，由客户端 LLM 自己读图，再调用 `commit_vision` 回写结构化 JSON。
  - 本地可选的分析组件：whisper、open_clip（用于 `find_similar`/`plan_swap` 找替换候选）、librosa。
- **审阅闭环与人工介入**：
  - 采用 plan → 审阅 → confirm → execute 的流程：执行前归档时间线版本，结果输出为新的 variant 时间线，并返回前后对比指标。
  - 破坏性操作默认 dry-run，必须带 `confirm_token` 才执行。
  - README 明确说明 `rank_takes` 只评估流畅度（口误、重启、脚本覆盖率），**不替人判断哪条 take 最好**。定位是"first-pass assembly"，最终剪辑由人在 Resolve 里完成。
- **渲染与许可**：由 Resolve 本体渲染，需要付费的 Resolve Studio。代码 MIT，可以借鉴工具设计；它强依赖桌面环境，不适合服务端直接依赖。

### 5. VideoDB Director（video-db/Director，MIT）

- **数据模型**：
  - 剪辑 Agent 把 VideoDB SDK 的类型签名整段放进系统提示词，由 LLM **生成 Python 代码**构造时间线。
  - 结构是 `Timeline{resolution, tracks[]}` → `Track(z_index).add_clip(start, clip)` → `Clip{asset, duration, transition, effect, filter, scale, opacity, fit, position}`。
  - 资产类型有 `VideoAsset(id, start, volume, crop)`、`AudioAsset`、`TextAsset`、`CaptionAsset`。入点用 `VideoAsset.start` 表示，时长用 `Clip.duration` 表示。
  - 来源：[backend/director/agents/editing/agent.py](https://github.com/video-db/Director/blob/main/backend/director/agents/editing/agent.py)
- **Agent 工具面**：
  - 两层结构：上层 reasoning engine 把每个 agent 当作工具调用；剪辑 agent 内部只有两个工具，`get_media` 和 `code_executor`。
  - 提示词写明 "You generate CODE, not JSON"，每次整份重写。
  - 代码执行方式是 `exec(code, {...})`，**没有沙箱**。报错会回灌给 LLM 重写，最多 25 轮。
  - 来源：[code_executor.py](https://github.com/video-db/Director/blob/main/backend/director/agents/editing/code_executor.py)
- **看素材**：
  - 由 VideoDB 服务端 `index_scenes` 逐帧用 VLM 生成场景描述；`prompt_clip` 把转写文本和场景描述交给 LLM 选片段。
  - 剪辑 agent 本身不看画面，也不回看成片，没有挑版本或重生成的逻辑。
  - 来源：[agents/index.py](https://github.com/video-db/Director/blob/main/backend/director/agents/index.py)、[agents/prompt_clip.py](https://github.com/video-db/Director/blob/main/backend/director/agents/prompt_clip.py)
- **渲染、人工介入与许可**：
  - VideoDB 云端生成 HLS 流，人通过聊天 UI 逐轮修改，没有门控。
  - 代码 MIT，但存储、索引、渲染全部依赖付费的 VideoDB 云。

### 6. Shotstack（商业云渲染 API）

- **数据模型**：Edit JSON，结构为 `Edit{timeline, output}` → `Timeline{tracks, fonts, background}` → `Track{clips}` → `Clip{asset, start, length, transition, effect, filter, fit, scale, position, opacity, alias}`，时间单位为秒。
  - "Smart clip"：`start: "auto"` 表示接在同轨上一段之后；`length: "auto" | "end"`；也可以用 `"alias://name"` 引用另一段。
  - `tracks[0]` 在最上层。同轨 clip 不能重叠。素材只接受公网 HTTPS URL。
  - 来源：[API reference](https://shotstack.io/docs/api/)、[Agent conventions](https://shotstack.io/docs/guide/agents/conventions.md)
- **Agent 工具面**：
  - 官方托管 MCP（Beta），工具有：`studio`、`render_video`（提交整份 Edit JSON）、`get_render_status`、`get_shotstack_guide`（返回 JSON 编写约定，要求 Agent 先调用）、模板相关工具。
  - CLI 配套 Claude Code Skill，规则是"先 `validate`（离线 schema 校验），再 `render`"。
  - 来源：[MCP server](https://shotstack.io/docs/guide/agents/mcp-server.md)、[CLI](https://shotstack.io/docs/guide/agents/cli.md)
- **看素材**：只有 `/probe` 返回元数据，另外可以渲染单帧图片。官方没有描述视觉复查闭环。
- **人工介入**：默认调用 `studio`，由人预览后点渲染。原文："Only call `render_video` directly when explicitly asked or when there's no human in the loop."
- **许可与定价**：
  - 云服务按分钟计费：订阅 $39/月起，折合约 $0.20/分钟；按量付费 $0.30/分钟。来源：[pricing](https://shotstack.io/pricing/)
  - Studio SDK 采用 PolyForm Shield 1.0.0，禁止用于与其竞争的业务。
  - 只宜借鉴 schema 与人审模式。

### 7. Remotion（自有许可证）

- **数据模型**：
  - 核心是 React 代码：`<Composition durationInFrames fps>`，加上 `<Sequence from durationInFrames>`，props 可以用 zod 校验。
  - 官方 prompt-to-video 模板使用 JSON 时间线 `{elements[], text[], audio[]}`，每个元素带 `startMs`、`endMs`。
  - 付费的 Editor Starter 状态为 `{tracks[], assets{}, items{}}`，同轨 item 不能重叠。
  - 来源：[Sequence](https://www.remotion.dev/docs/sequence)、[Editor Starter state](https://www.remotion.dev/docs/editor-starter/state-management)
- **Agent 工具面**：
  - 主推的方式是让编程 Agent 写 TSX，配合官方 Agent Skills。
  - **Remotion MCP 已废弃**。它原本只做文档检索，官方给的理由包括 agent 调用不可靠、与 skill 重复。来源：[docs/ai/mcp](https://www.remotion.dev/docs/ai/mcp)
  - WebMCP（v4.0.518 起）只提供读取和播放控制，例如 `get_sequences` 返回各段的起止帧，**没有写内容的工具**。来源：[docs/ai/webmcp](https://www.remotion.dev/docs/ai/webmcp)
  - 官方 motion-graphics 模板的后续修改走 `{type:"edit"|"full", edits[{old_string,new_string}]}` 补丁；编译失败会带着错误自动重试，并把当前帧截图（`frameImages`）附在后续请求里。
- **看素材**：可以用 `npx remotion still` 或 `render --frames=...` 导出指定帧给 Agent 看。官方没有分析原始素材画面的能力。
- **渲染**：无头 Chromium 逐帧截图，再用 FFmpeg 编码；可选 Lambda 分块并行渲染。
- **许可**：
  - "Remotion License" 不是开源许可证。个人和员工不超过 3 人的组织可以免费使用；其他组织需要 Company License。
  - 定价：Automators 档 $0.01/次渲染，每月最低 $100。官方原话把"做视频编辑器、prompt-to-video 工具、自动化流水线的组织"归入这一档。
  - 明确禁止"允许用户把任意 Remotion 项目提交到你的服务器渲染"。
  - 来源：[LICENSE.md](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md)、[license FAQ](https://www.remotion.dev/docs/license/faq)
  - 结论：**不宜作为渲染或预览的依赖**，只借鉴思路。

### 8. NarratoAI（linyqh/NarratoAI，MIT）

- **数据模型**：
  - 扁平 JSON 片段数组，每项字段为 `{_id, timestamp:"HH:MM:SS,mmm-HH:MM:SS,mmm", picture, narration, OST}`。
  - `OST` 三态：0 = 只要解说、去掉原声；1 = 播原片原声；2 = 解说与原声并存。
  - 渲染时按 OST 决定裁切依据：0 和 2 按 TTS 时长裁，1 按时间戳裁。
  - 来源：[app/utils/check_script.py#L43](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/utils/check_script.py#L43)、[app/services/task.py#L348-L355](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/task.py#L348-L355)
- **Agent 工具面**：
  - 固定流水线，每一步单次提示词输出整份 JSON，没有工具循环。
  - 提示词写明"严禁虚构时间戳，只能从 `<video_frame_description>` 中提取"。
  - 另有确定性校验器，检查：时间戳越界、片段重叠、解说字数超过画面时长、OST=1 的解说必须以"播放原片"开头。
  - `script_repair` 提示词已经写好，但没有接到 WebUI 上。
  - 来源：[narration_generation.py#L91-L106](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/prompts/documentary/narration_generation.py#L91-L106)、[short_drama_narration_validation.py](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/short_drama_narration_validation.py#L301-L400)
- **看素材**：
  - 纪录片模式下，ffmpeg 每 3 秒抽一帧，每 10 帧一批送进 VLM，得到 `frame_observations[]`。每批的时间范围和摘要直接成为一个片段的 `timestamp` 和 `picture`。
  - 视觉后端可选 TwelveLabs Pegasus。
  - 看画面只用于理解素材，没有审片闭环。
  - 来源：[frame_analysis_service.py](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/documentary/frame_analysis_service.py)
- **渲染与人工介入**：
  - 渲染：ffmpeg 切片加 concat，MoviePy 合成字幕和音频。另有**剪映草稿导出**：[jianying_draft_builder.py](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/jianying_draft_builder.py)
  - 人工介入：人可以编辑剧情理解文本；文案审核通过后才做画面匹配；剪辑脚本在 Streamlit 表格里编辑。

### 9. FunClip（modelscope/FunClip，MIT）

- **做法**：
  - 先用 ASR 得到字级时间戳和 SRT，再把整份 SRT 交给 LLM 单次调用。
  - LLM 输出 `1. [开始-结束] 文本` 格式的文本，由正则解析成 `[[start_ms,end_ms],...]`。
  - 用 MoviePy 做 subclip 加 concat。
  - 来源：[funclip/utils/trans_utils.py#L125-L136](https://github.com/modelscope/FunClip/blob/7e4b15fe2233680edaf5ec92bf18e2071e499b69/funclip/utils/trans_utils.py#L125-L136)、[funclip/launch.py#L249-L254](https://github.com/modelscope/FunClip/blob/7e4b15fe2233680edaf5ec92bf18e2071e499b69/funclip/launch.py#L249-L254)
- **看素材**：默认不看。可选 TwelveLabs Pegasus 看整段视频挑高光，输出同样的 `[start-end]` 格式。
- **人工介入**：LLM 的结果放在可编辑文本框里，人可以改时间戳或删行后再执行；有起止偏移滑块。
- 许可 MIT，但 FunASR 等模型权重的许可需要单独确认，未核实。

### 10. Twelve Labs Jockey（原 twelvelabs-io/tl-jockey，上游已下线）

- **现状与许可**：上游仓库目前返回 404。依据 [Wayback 快照](http://web.archive.org/web/20251029131815/https://github.com/twelvelabs-io/tl-jockey)，许可证为 Apache-2.0，LICENSE 原文未核实。代码取自保留了上游历史的第三方镜像 [archive-superdisco/twlv@6b6bcfe0](https://github.com/archive-superdisco/twlv/tree/6b6bcfe030d6691419af6cd79ef27e1dad51dddb)。
- **图结构与工具**：
  - LangGraph 图为 supervisor → planner → worker → reflect。
  - worker 的工具有：`simple-video-search`（Marengo 搜索）、`gist/summarize/freeform-text-generation`（Pegasus）、`combine-clips`（用 ffmpeg 拼接）。
- **关键设计**：
  - 搜索结果 `Clip{video_id, start, end, score, ...}` 存进图状态。
  - planner **只选 `clip_keys`**；worker 节点从状态里注入真实的 `start/end`，**避免 LLM 编造时间码**。
  - 来源：镜像 `jockey/jockey_graph.py`（约 L311-L319、L382-L386）
- 没有审阅闭环，也没有门控，人通过下一轮对话继续。

### 11. 基线与剪映系（简述）

- **MoneyPrinterTurbo（MIT）**：
  - LLM 只产出文案和素材搜索词，完全不接触时间线。按固定时长切 stock 素材，用 MoviePy 加 ffmpeg 拼接。
  - 这是"没有剪辑决策模型"的最低基线。
  - 来源：[app/services/llm.py](https://github.com/harry0703/MoneyPrinterTurbo/blob/f2d44d62721aeaecb1898488a3bc06399da2168d/app/services/llm.py)
- **editly（MIT）**：
  - 声明式 JSON5 剪辑规格：`clips[]` 顺序串联，每个 clip 内叠多个 `layers[]`；另有 `audioTracks[]`、`audioNorm`。
  - 视频层用 `cutFrom/cutTo` 截取素材。
  - 用 node-canvas、headless-gl 加 ffmpeg 渲染。维护不活跃，最后提交为 2025-02。
  - 来源：[README#edit-spec](https://github.com/mifi/editly#edit-spec)
- **剪映草稿 MCP**：
  - [sun-guannan/VectCutAPI](https://github.com/sun-guannan/VectCutAPI)：Apache-2.0，约 2.2k star。`mcp_server.py` 提供操作式工具：`create_draft`、`add_video(video_url, start, end, target_start, track_name, transition, speed, ...)`、`add_audio`、`add_text`、`add_subtitle`、`add_effect`、`save_draft`。
  - [hey-jian-wei/jianying-mcp](https://github.com/hey-jian-wei/jianying-mcp)：无 LICENSE 文件。
  - 两者底层都是 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)（Apache-2.0），而且都**不看画面**。VectCutAPI 的 README 说明其 "MCP Editing Agent" 本身没有开源。

## 三、横向对比

| 实现 | 类型 / 许可 | 剪辑决策数据模型 | LLM 怎么写 / 怎么"看见"时间线 | 渲染后端 | 看素材与审阅闭环 | 人工介入 |
|---|---|---|---|---|---|---|
| **OpenMontage** | 开源 / AGPL-3.0 | `edit_decisions` JSON：`cuts[]{source,in/out,transition,layer,transform,reason}` + `audio{narration,music(ducking),sfx}` + `subtitles` + `overlays` | 宿主 LLM 整份写 JSON artifact，按 schema 校验；读检查点 JSON；edit 阶段不开放工具 | Remotion / HyperFrames / FFmpeg，按 `render_runtime` 路由 | 抽帧后由宿主多模态 LLM 读 PNG；成片自检（ffprobe、4 帧黑帧、音频、转写比对）；渲染前查幻灯片风险；每阶段最多修订 3 次 | 门控阶段强制审批（写检查点时校验）；只读 Backlot 看板；聊天审批；预算闸门 |
| **Descript Underlord** | 商业闭源 | 以转写文本为主轴，加 Scene / composition；内部 JSON 未公开 | 聊天 co-editor，感知当前选区；对外只有一个 `/jobs/agent` 端点和 MCP | 自有云端 | 抽帧 → VLM → 按时间戳索引的描述文本，用于找镜头和放 B-roll | 每次修改前建 checkpoint，每条回复可 Revert，另有版本历史 |
| **Diffusion Studio editor** | 开源 / MPL-2.0 | JSX 即时间线：`start/end` + `sourceIn/sourceOut` + `id` | 直接改 JSX 文件；MCP 只提供看、查、导出工具 | WebCodecs + Mediabunny（本地） | `capture` 带时间码的联系表、`check` 确定性体检、`media_filmstrip/waveform`；capture 加 check 通过后再 export | GUI 与代码双向写回 |
| **DaVinci Resolve MCP** | 开源 / MIT（需付费的 Resolve Studio） | Resolve 原生时间线；序列化为 `[{id,start,end,duration,kind}]` | **操作式工具**：`ripple_insert / move_clips / delete_clips / execute_swap`…，返回操作信封 | Resolve 本体 | `timeline_frame.capture` 返回图像；客户端读帧后回写 `commit_vision`；`rank_takes` 只评估流畅度；`plan_swap` 按相似度给出替换候选 | plan → confirm → execute；默认 dry-run 加 `confirm_token`；改前归档版本，输出 variant 时间线 |
| **VideoDB Director** | 开源 / MIT（依赖付费云） | VideoDB SDK `Timeline→Track→Clip→Asset` | LLM 生成 Python 代码，整份重写，用 `exec` 执行（无沙箱）；报错回灌，最多 25 轮 | VideoDB 云端生成 HLS | 服务端逐帧用 VLM 建场景索引，供选片；不回看成片 | 聊天逐轮修改，无门控 |
| **Shotstack** | 商业 SaaS（Studio SDK 为 PolyForm Shield） | Edit JSON `timeline→tracks→clips{asset,start,length,transition}`，支持 smart clip `auto/alias` | MCP 一次提交整份 JSON；先 `get_shotstack_guide`，先 `validate` | 自有云渲染，按分钟计费 | 只有 probe 和单帧渲染，没有视觉闭环 | 默认先开 `studio` 给人预览，再渲染 |
| **Remotion** | 自有许可证（超过 3 人的公司需付费） | React TSX `<Sequence from durationInFrames>`；模板用 JSON 时间线 | 编程 Agent 写代码或 old/new 补丁；WebMCP 只读 | 无头 Chromium + FFmpeg，可选 Lambda | `still` 或指定帧截图附给模型 | Studio 里的 GUI 编辑写回源码 |
| **NarratoAI** | 开源 / MIT | 扁平数组 `{timestamp, picture, narration, OST 三态}` | 单次提示词整份 JSON；禁止虚构时间戳，另有确定性校验器 | ffmpeg + MoviePy；**另可导出剪映草稿** | 纪录片模式抽帧后分批送 VLM 生成描述；不审成片 | 可编辑中间文本，用表格编辑剪辑脚本 |
| **FunClip** | 开源 / MIT | 文本行 `[start-end] 文本`，正则解析 | 单次调用，读 SRT | MoviePy | 可选 Pegasus 挑高光 | 可编辑结果文本框，有偏移滑块 |
| **Jockey** | 开源 / Apache-2.0（上游已下线） | 扁平 `Clip{video_id,start,end,score}` | LangGraph 多节点；**LLM 只选 key，时间码由状态注入** | 本地 ffmpeg concat | Marengo 检索、Pegasus 摘要；不审成片 | 下一轮对话 |
| **剪映系 MCP**（VectCutAPI 等） | 开源 / Apache-2.0 | 剪映草稿（经 pyJianYingDraft） | 操作式 `add_video/add_audio/add_text…` | 剪映本体 | 不看 | 在剪映里精修 |

## 四、对 ArcReel 的借鉴清单（对照地图已定方向）

### A. 剪辑时间线作为唯一真相源

1. **字段骨架可以直接参照 OpenMontage 的 `edit_decisions`，再补上与 ArcReel 相关的差异。**
   - 按轨道分：主画面 cut、旁白段、BGM（音量、淡入淡出、ducking）、字幕、音效。
   - 每个 cut 带 `source`、`in/out`、`transition`、`reason`。
   - 差异一：`source` 应该引用**产物与版本**，而不是文件路径，这样 L2 挑版本时只需改引用。
   - 差异二：每个 cut 要能追溯到剧本单元，与现有呈现模型的单元对齐。
   - 这套骨架与 pyJianYingDraft 的 track/segment 模型可以一一映射；VectCutAPI 的 `add_video(start, end, target_start, track_name, transition, speed)` 就是现成的参照。
2. **主轨用相对排布，由服务端计算绝对时间。**
   - OpenMontage 的 cut 按顺序首尾相接，Shotstack 用 `start:"auto"`，都是这个思路：让 LLM 只表达顺序和入出点，避免它做时间算术出错。
   - 旁白、BGM、字幕这类附属轨需要绝对时间或锚点，可以参照 Shotstack 的 `alias://` 做"锚定到某个 cut"。
3. **原声、旁白、BGM 的三态开关值得参考 NarratoAI 的 `OST`。**
   - `OST` 有三种取值：只要解说、只要原声、两者并存。它把"原声与旁白怎么共存"显式建模到每个片段上。这正好对应地图「Not yet specified」里的混音策略，以及 #1435（原声与 TTS 旁白直接叠放）。
   - OpenMontage 的 `music.ducking` 可以作为 BGM 的最小字段。
4. **把决策理由写进时间线，而不是只留在对话里。**
   - OpenMontage 的 `cut.reason` 和 `metadata.reorder_notes` 让首轮自动剪辑结果可以解释。用户对话追问"为什么这么剪"时，Agent 可以直接引用这些字段。

### B. Agent 工具面（L1 剪辑决策）

5. **首版整份生成，后续改动走操作式工具，两者混合使用。**
   - 首轮由 Agent 自动出一版时，整份生成、再按 schema 校验最简单，OpenMontage、Shotstack、NarratoAI 都这样做。
   - 对话式修改（"第 3 镜短 1 秒""这里换叠化"）适合用少量**操作式 MCP 工具**：裁切、移动、替换素材版本、设置转场、设置音轨参数。依据有三点：
     - Resolve MCP 的操作式设计支持局部修改、可以校验、容易生成改动差异；
     - Remotion 的整份代码或补丁方式需要编译重试来兜底；
     - Director 的整份代码重写需要不带沙箱的 `exec`，属于反例。
   - 每次操作返回类似 Resolve MCP `_operation` 的信封：做了什么改动、校验结果、新版本号。
6. **把时间线序列化成紧凑文本给 LLM 看，并用 ID 引用。**
   - Resolve MCP 的 `get_items` 返回 `[{id,start,end,duration,kind}]`，Remotion WebMCP 的 `get_sequences` 返回各段起止帧，两者都是先把时间线压成一张短表再交给 LLM。
   - ArcReel 可以提供一个读取工具，按 cut id 返回时长、素材版本、转场、所属剧本单元。
7. **时间码由服务端注入或校验，不让 LLM 凭空编写。**
   - Jockey 让 LLM 只选 `clip_keys`，时间码从状态里注入。
   - NarratoAI 在提示词里禁止虚构时间戳，并配一个确定性校验器检查越界、重叠、字数与时长。
   - ArcReel 的入出点校验应该放在服务端的时间线写入口，而不是依赖 skill 文本。
8. **把剪辑手法写成 skill，按剧本布局区分。**
   - OpenMontage 为每条流水线写一个 `edit-director.md`，内容包括节奏网格、找最好的子窗口、两端留余量、转场词汇表、相邻镜头多样性、"重大改动必须先问"。
   - ArcReel 的四种剧本布局可以各自配一份剪辑 skill。skill 是纯文本，可以借鉴思路，但不要复制原文。

### C. L2 看素材剪

9. **优先采用"联系表加宿主多模态 Agent 读图"，再按需补充"帧描述缓存"。**
   - ArcReel 的内嵌 Agent 基于 Claude Agent SDK，本身就能读图。
   - 最便宜的做法是提供一个工具：对某个 cut 或某个素材版本渲染**带时间码的联系表**。Diffusion Studio `capture` 的实测上限是 2576×1456；OpenMontage 和 Resolve MCP 都走"服务端抽帧、客户端读图"的路线。
   - 如果需要在整集范围里按画面内容检索，再加 Descript、NarratoAI 那种"抽帧 → VLM → 带时间戳的描述文本"缓存。
   - 没有必要引入 Twelve Labs 这类视频原生模型的外部依赖。
10. **挑版本和裁坏帧可以由 Agent 提议，重生成必须由人确认。**
    - 所有调研对象都没有自动重生成。Resolve MCP 的 `rank_takes` 明确不判断"哪条最好"，它的流程是 plan 加 confirm。
    - 这与地图的决策"L2 触发重生成必须由用户确认"一致。可以把重生成做成 plan 类操作：给出建议和理由，用户确认后再调用现有生成链路。
11. **成片或预览渲染之后跑一遍确定性自检，再交给人看。**
    - OpenMontage 会用 ffprobe 检查是否有音频流、时长误差、4 帧黑帧、音频静音或削波。
    - Diffusion Studio 的 `check` 查黑帧空隙和加载失败的素材。
    - 这类检查不耗费模型额度，适合做成时间线渲染器的内置步骤，结果回传给 Agent。

### D. 对话式介入，精修交给剪映

12. **只读预览加对话审批，已经有成熟先例。**
    - OpenMontage 的 Backlot 是只读看板，所有状态从检查点推导，审批在聊天里完成。这与地图"ArcReel 内只做只读预览，不做可编辑的时间线编辑器"的定位一致。
13. **每次 Agent 修改都生成时间线版本，并且可以一键回退。**
    - Descript 在每轮修改前建 checkpoint，并提供 Revert；Resolve MCP 改前先归档，结果输出为 variant。
    - ArcReel 的剪辑时间线应该带版本，每次 Agent 修改产生一个新版本，前端预览可以对比和回退。
14. **破坏性或高成本操作先预览、再确认。**
    - Shotstack 的做法是"先 `studio` 给人看，再 `render`"；Resolve MCP 的做法是 dry-run 加 `confirm_token`。
    - ArcReel 在出成片（耗时的 ffmpeg 渲染）和触发重生成之前，都可以采用"先给预览或计划，再确认"。
15. **剪映草稿与成片从同一份时间线导出，已有先例。**
    - NarratoAI 同一份剪辑脚本既渲染 mp4，也导出剪映草稿。OpenMontage 同一份 `edit_decisions` 路由到多个渲染器。
    - 剪映系 MCP（VectCutAPI、jianying-mcp）说明 pyJianYingDraft 足以承载多轨、转场、字幕、关键帧。只要剪辑时间线的字段不超出剪映草稿能表达的范围，导出就可以保持无损。

### E. 依赖与许可结论

| 对象 | 结论 |
|---|---|
| pyJianYingDraft（Apache-2.0） | 已在用，继续作为剪映草稿导出后端 |
| ffmpeg | 成片渲染后端的首选；运行环境可能缺少可执行文件，这是已知约束 |
| Remotion | **不引入**。它的 Player 和渲染都算 automation，需要付费许可，并禁止开放任意项目渲染 |
| Shotstack、Descript、VideoDB | 云端 SaaS 或闭源，**不引入**，只借鉴 schema 与交互 |
| OpenMontage（AGPL-3.0） | 许可证兼容，但它不是库，**借鉴** schema、skill 手法、门控和自检 |
| Diffusion Studio editor（MPL-2.0）、Resolve MCP（MIT）、NarratoAI（MIT） | **借鉴**工具面设计：联系表、check、操作信封、plan/confirm、OST 三态 |
| editly（MIT） | 规格格式可以参考；维护不活跃，不引入 |

## 五、未核实与局限

- Descript 内部时间线 JSON 与 Underlord 内部工具名没有公开。
- Shotstack 渲染引擎的实现没有公开。
- Jockey 上游的 LICENSE 原文与下线原因未核实；Twelve Labs v1.2 API 当前是否可用未核实。
- FunClip 所依赖模型权重的许可、NarratoAI 所接第三方服务的条款，都没有逐一核实。
- NarratoAI 开源版与其托管版的功能差异未核实。
- Diffusion Studio 旧版 core 的去水印 license key 价格未核实。
- 本文只调研了公开实现。商业产品的实际效果（剪辑质量、看素材的准确度）没有做实测。
