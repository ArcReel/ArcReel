# Agent 剪辑的具体工具面：看素材与时间线管理

> 本文是 [agent-editing-prior-art.md](https://github.com/ArcReel/ArcReel/blob/research/agent-editing-prior-art/docs/research/agent-editing-prior-art.md)（分支 `research/agent-editing-prior-art`）的纵深续篇。前文回答"谁在做、怎么做"，本文只看**具体工具面**：工具名、参数、返回形态、默认值、闸门，并给出 ArcReel 的落地建议。
>
> 两个问题：
>
> - **Part A：L2"看素材"。**帧与联系表怎么生成，图像怎么交给模型，算了哪些确定性信号，结果怎么进入决策，token 成本怎么控。
> - **Part B：时间线管理。**创建、复制/变体、改名、列表、版本历史、回滚、删除；破坏性操作是否开放给 Agent、用什么闸门；Agent 怎么触发渲染/导出，渲染前有没有预览闸门。
>
> 只陈述一手事实（源码、工具 schema、官方文档）。源码链接固定到调研当日（2026-09-28）默认分支的 commit：
>
> | 仓库 | commit | 许可 |
> |---|---|---|
> | samuelgursky/davinci-resolve-mcp | `89da04b1` | MIT |
> | diffusionstudio/editor | `666cdced` | MPL-2.0 |
> | calesthio/OpenMontage | `08e2151f` | AGPL-3.0 |
> | linyqh/NarratoAI | `9fa69e02` | MIT |
> | video-db/Director | `70e0b3df` | MIT |
> | sun-guannan/VectCutAPI | `7e5b9c40` | Apache-2.0 |
> | hey-jian-wei/jianying-mcp | `f312beac` | 无 LICENSE 文件 |
>
> Descript、Shotstack 只看公开文档。

[resolve]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23
[ds]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a
[om]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7
[narrato]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1
[director]: https://github.com/video-db/Director/blob/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f
[vectcut]: https://github.com/sun-guannan/VectCutAPI/blob/7e5b9c407faa933163b867711a793f343d22bb04
[jymcp]: https://github.com/hey-jian-wei/jianying-mcp/blob/f312beac6091c9580e85edb4094878916b7b983d

## 一、核心结论

1. **图像交付有三种形态，只有"MCP 内联图像块"不依赖 Agent 读文件。**
   - Diffusion Studio：结果≤4 张、每张≤1 MB 时，PNG 以 MCP `image` 块内联返回，同时返回路径和 JSON；超限则只给路径。
   - Resolve MCP：`timeline_frame.capture` 直接返回 MCP 图像；但素材分析默认走 `host_chat_paths`，只返回帧的绝对路径，要求宿主 Agent 自己读图，再调 `commit_vision` 回写 JSON。
   - OpenMontage、NarratoAI、Director：分别是"返回路径让宿主读""VLM 生成文字""只推给前端展示、模型只收到文字"。
2. **联系表的事实标准是"≤12 格、每格烧录时间码、整张不超过视觉模型的全细节上限"。**
   - Diffusion Studio `capture`/`media_grab` 默认合成联系表，每张最多 12 格，整张不超过 2576×1456，官方原话是"the largest image a vision model reads at full detail"。
   - 这个上限与 Claude 高分辨率档完全对应：长边 2576 px、最多 4784 个视觉 token（Claude 4.7 及以后的模型）。也就是一张满尺寸联系表约 4.7k token。
   - Resolve 的 `thumbnail_contact_sheet` 默认 4 列、最多 12 个采样点，每格带标签，但只返回文件路径。
3. **确定性信号几乎都用 ffmpeg 滤镜，先于任何视觉模型运行。**
   - Resolve：`blackdetect=d=0.5:pix_th=0.10`、`silencedetect=noise=-50dB:d=1`、`ebur128` 响度、`select='gt(scene,0)'` 场景分数加自适应阈值、`idet` 隔行。
   - Diffusion Studio：`check` 是**不渲染**的结构检查（无视觉覆盖的时段、从不可见的节点、零时长、全透明、素材加载失败）；`media_waveform` 返回静音段。
   - OpenMontage：成片自检抽 10%/35%/65%/90% 四帧，黑帧判定是**PNG 文件小于 2000 字节**这种启发式；音频用 `volumedetect`（均值 < -60 dB 视为静音，峰值 > -0.5 dB 视为削波）。
   - 所有调研对象都没有用 `freezedetect`（冻帧）。
4. **"看"之后能直接改时间线的只有 Resolve，而且它刻意不替人挑 take。**`rank_takes` 只给流畅度排名和证据，源码注释明确说不输出"best take"。`plan_swap` 用相似度索引给替换候选，`execute_swap` 需要 confirm token。
5. **时间线管理：Resolve 是唯一完整实现"版本链 + 回滚 + 变体 + 删除闸门"的。**
   - 每次破坏性操作前自动把工作时间线复制进 `Archive` 素材箱，同一个 `analysis_run_id` 内只归档一次。
   - 回滚本身是一次带版本的操作：先归档当前状态，再把旧版本复制成 `<name>_rolled_back_<HHMMSS>`，不覆盖原名。
   - `edit_engine` 的所有 `execute_*` 都产出**新名字的变体时间线**，原时间线不动。
   - 删除时间线、删除轨道、ripple 删除等"灾难性"操作走 `confirm_token`：首次调用只返回预览加 token；token 一次性、5 分钟过期、绑定动作名和参数指纹。
6. **其他实现的时间线管理都很薄。**
   - NarratoAI：每次保存生成一个带时间戳的新 JSON 文件，按创建时间列出，没有改名、删除和版本概念。
   - Diffusion Studio：时间线就是 JSX 文件，工具面里没有历史、撤销或删除工具。
   - 剪映系 MCP：只有 `create_draft`、`add_*`、`save_draft/export_draft`，没有列表、改名、删除，也没有删除片段的工具。
   - Descript：每条 Underlord 回复下有 Revert；对外 API 只有一个异步 agent 任务端点。
7. **渲染触发：一律"默认不渲染，用户要求才渲染"；长任务一律异步 + 轮询。**
   - Diffusion Studio 的 editor skill："DO NOT export/render the scene for visual confirmation — `capture` is equivalent to a render but far more efficient"。`export` 是同步阻塞调用（CLI 最多等 60 分钟），同一时间只允许一个导出。
   - Shotstack MCP：默认调用 `studio` 让人在 iframe 里点渲染；`render_video` 只在用户明确要求或没有人参与时使用，之后用 `get_render_status` 轮询 `queued/fetching/rendering/saving/done/failed`。
   - Resolve：长操作传 `background=true` 返回 `job_id`，再用 `resolve_control(action="job_status")` 轮询；渲染完用 `verify_output` 检查真实输出文件，因为 Resolve 的 JobStatus 会对几乎为空的产物也报 Complete。
   - Descript API：`POST /v1/jobs/agent` 立即返回 `job_id`，`GET /v1/jobs/{job_id}` 轮询 `running/stopped`，也支持 `callback_url`；发布走单独的 `POST /v1/jobs/publish`。

## 二、Part A：看素材

### A1. Diffusion Studio editor（最完整的"看"工具面）

工具目录与返回约定见 [docs/reference/tools/README.md][ds-readme]。所有工具返回一个 JSON 对象（MCP 的 `structuredContent`，同时复制为一个 text 块）。渲染图像的工具把 PNG 写到磁盘并返回路径，"over MCP a result of at most four images, none over a megabyte, also carries them inline as image content"。

[ds-readme]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/reference/tools/README.md

实现位于 [apps/desktop/src/dapi/present.ts][ds-present]：`INLINE_MAX_IMAGES = 4`，`INLINE_MAX_BYTES = 1 << 20`。`toCallToolResult` 先放一个 JSON text 块，满足条件时再追加 `{type:"image", data:<base64>, mimeType:"image/png"}`。错误以 `isError: true` 加一句可读文本返回，不走协议错误。

[ds-present]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/apps/desktop/src/dapi/present.ts#L19-L136

| 工具 | 输入 | 产出 | 说明 |
|---|---|---|---|
| [`capture`][ds-capture] | `id`（scene）、`times: Time[]`（默认 `[0]`）、`separate`、`perSheet`（1–12）、`output` | `{images:[{timecode, path}]}` | 渲染"导出时会编码的那一帧"（合成后画面）。默认合成联系表 |
| [`check`][ds-check] | `id` | `{stats, issues[]}` | 不渲染、不花额度的结构检查 |
| [`media_grab`][ds-grab] | `path`、`times` / `count` / `auto`、`start/end`、`quality`、`separate`、`perSheet`、`uncapped` | 同 capture | 取素材本身的像素 |
| [`media_filmstrip`][ds-film] | `path`、`start/end`、`scale`（0.25–4） | `{path}` | 均匀采样缩略图网格，每行带 `HH:MM:SS:FF` 标尺 |
| [`media_waveform`][ds-wave] | `path`、`start/end`、`scale` | `{path, silences:[{start,end}]}` | 响度波形，静音段标红并以秒返回 |
| `media_probe` / `media_transcribe` / `media_listen` | — | — | 容器与轨道信息 / 字级时间戳转写（写文件返回路径）/ 音频模型问答（花额度） |

[ds-capture]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/reference/tools/capture.md
[ds-check]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/reference/tools/check.md
[ds-grab]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/reference/tools/media/grab.md
[ds-film]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/reference/tools/media/filmstrip.md
[ds-wave]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/reference/tools/media/waveform.md

**联系表规格（`capture`）：**

- 每张最多 12 格，每格标注时间码。时间码去掉零段，例如 `08s10f`、`01m05s`，起始帧是 `0f`。
- 整张"never exceeds 2576x1456"。在此预算内选"让每帧画得最大"的网格；外边距 4px，格间距 8px，透明背景合成到灰底。
- 16:9 下的每格尺寸：1 格 1920×1080；2–4 格 1280×720；5–9 格 850×478；10–12 格 636×357。
- `separate: true` 时每个时间点单独一张，高 720p。
- 帧严格按导出顺序向前推进渲染，所以依赖播放状态的动画也与导出一致。

**`media_grab` 的采样与分辨率：**

- 三种采样互斥：`times`（负数从末尾倒数，如 `-1f`）；`count`（窗口内均匀取 N 帧）；`auto`（以 2 fps 扫描，画面"稳定进入新视觉状态"时取一帧，跳过转场和近重复帧，默认最多 30 帧，需要 WebGPU）。
- `quality` 是像素预算：`small` 384²（16:9 为 512×288）、`medium` 768²（1024×576）、`large` 1536²（2048×1152）、`fullres`。
- 默认有 100 帧安全上限，`uncapped` 解除。
- 官方建议："past ~12 frames prefer media_filmstrip"（后者更省 token）。

**`check` 的问题码：**

| code | 严重度 | 含义 |
|---|---|---|
| `black-frames` | error | 一帧以上没有任何视觉元素被排上，返回 `ranges` |
| `no-visuals` | error（只有音频时为 warning） | 子树完全不出画 |
| `never-visible` | warning | 节点被排在祖先播放窗口之外 |
| `zero-duration` | warning | 零时长节点 |
| `transparent` | warning | 静态不透明度为 0 |
| `source-error` | error | 素材加载或生成失败 |

文档明确说：它只能判断"没有排东西"，不能判断"画面是黑的"（例如素材本身很暗），可疑时段要再用 `capture` 目检。

**决策用法（skill 文本）：**

- [docs/skills/editor.md][ds-editor-skill]：先 `check`，再 `capture`，把截到的帧与 brief 对照；"Fix the largest viewer-facing problem before polishing details"；不要为了视觉确认而导出。
- [docs/skills/watch.md][ds-watch-skill]：先 `media_probe`；再用 waveform 加 filmstrip 看全局；按转写或音频模型给出的时间点定向 `media_grab`，没有线索时才用 `auto`；"Read only as much of the footage as the answer requires"。

[ds-editor-skill]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/skills/editor.md
[ds-watch-skill]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/skills/watch.md

### A2. DaVinci Resolve MCP

**`timeline_frame(action="capture")`**：看 Resolve 处理后的输出，包括调色、Fusion、字幕、转场。见 [src/server.py#L26564-L26625][r-tf]。

[r-tf]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L26564-L26625

- 参数：`timecode?|frame?`、`quality`、`max_width`（控制上下文成本；需要 ffmpeg，没有 ffmpeg 就报错，而不是悄悄返回全尺寸）、`format`（默认 jpg）、`timeline_name`（临时切过去，读完再切回）。
- `quality` 的取舍：
  - `frame`（默认）：逐帧精确、全分辨率，约 1 秒；会改项目渲染设置再尽量恢复；有其他渲染在跑时拒绝执行。
  - `preview`：同样的渲染，但宽度限制在 1280。
  - `thumbnail`：瞬时、无副作用，但**不是逐帧精确**，同一片段内每一帧返回的都是同一张缩略图。
  - `still`：画廊静帧，需要画廊面板处于打开状态。
- 返回 FastMCP `Image(data=..., format=...)`，即 MCP 图像内容。
- 使用场景写在 docstring 的 `<when_to_use>`：核对标题位置与安全区、确认剪辑点落在预期位置、在同一时间码做改前改后对比。

**`timeline(action="thumbnail_contact_sheet")`**：见 [src/server.py#L7117-L7220][r-cs]。

[r-cs]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L7090-L7220

- 采样点来自 `frames` 参数或时间线标记，`max_samples` 默认 12。参数 `columns` 默认 4、`padding` 默认 8、`label_height` 默认 24。
- 用播放头逐点移动并轮询缩略图，结束后恢复播放头。如果播放头移动被拒绝，这一格记为错误，而不是用错误的帧顶替。
- 返回 `{path, metadata_path, width, height, sample_count, ...}`，**只给路径**。附带的 `review_guidance` 写着："Treat contact sheets as review evidence, not final visual analysis."

**`media_analysis` 与 `host_chat_paths` 协议**：

- 服务器指令（[src/server.py#L400-L445][r-instr]）写明，视觉分析默认走 `host_chat_paths`：分析动作返回一个延迟负载，其中带绝对路径 `frame_paths`，"you must read those frames as images and call media_analysis(action="commit_vision", ...) to finalize"。不完成 `commit_vision` 就停留在 `pending_host_vision_analysis`，被视为失败状态。
- 负载由 `build_host_chat_paths_payload` 生成，见 [src/utils/media_analysis.py#L4675-L4800][r-hcp]。内容包括：
  - 每帧元数据：`frame_index`、`time_seconds`、`selection_reason`、`delta_from_previous`，以及 `cut_index`、`boundary_role`、`shot_index` 等；
  - 镜头表：每个镜头的起止时间和对应帧号；
  - 剪辑点摘要：`cut_count`、`flash_frame_candidates`；
  - 分析提示词、响应 schema、`commit_action`；
  - 一个 `vision_token`，它是 clip、文件、帧路径和分析版本的哈希，用于防止回写到错误的素材。
- 采样模式（[src/utils/media_analysis.py#L740-L760][r-samp]）：`fixed`（Economy）、`per_minute`（Balanced）、`adaptive_capped`（Thorough，产品推荐）、`adaptive`（无上限）。默认每分钟 4 帧，下限 3 帧，上限 80 帧。
- **预算闸门**（[src/utils/analysis_caps.py][r-caps]）：
  - 按每帧约 1000 token 预估（`AVG_VISION_TOKENS_PER_FRAME = 1000`），超出累计上限就在调用前拒绝，而不是事后才发现超支。
  - 预设四档：

    | 档位 | 每片段帧数 | 每片段 token | 每天 token | 帧最大边长 |
    |---|---|---|---|---|
    | minimal | 12 | 16k | 150k | 512 px |
    | standard | 80 | 100k | 2M | 768 px |
    | generous | 200 | 250k | 6M | 1280 px |
    | unlimited | 不限 | 不限 | 不限 | 不限 |

[r-instr]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L400-L445
[r-hcp]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/media_analysis.py#L4675-L4800
[r-samp]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/media_analysis.py#L740-L760
[r-caps]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/analysis_caps.py

**确定性信号**：在 `_readthrough_analysis` 中一次跑完（[src/utils/media_analysis.py#L2923-L2972][r-rt]）。

- 响度：`ebur128=peak=true`。
- 场景切换：`select='gt(scene,0)'` 输出每帧场景分数，再按分布算自适应阈值。注释说这替换了原来写死的 `gt(scene,0.3)`，因为固定阈值在高运动素材上漏检、在固定机位素材上过敏。
- 黑帧：`blackdetect=d=0.5:pix_th=0.10`。
- 静音：`silencedetect=noise=-50dB:d=1`。
- 隔行：`idet`。
- `edit_engine.plan_silence_ripple` 也用 `silencedetect` 提出静音剪除计划。

[r-rt]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/media_analysis.py#L2923-L2972

**看完之后怎么进入决策**：`edit_engine` 提供 plan → confirm → execute 三段式循环（[src/server.py#L24309-L24345][r-ee]）。

- `plan_selects`：按深度分析得到的"select potential"给镜头排序，按故事主线排列，两端留余量（handle）。
- `plan_tighten`：依据转写间隙剪掉无声段。
- `plan_swap`：用相似度索引为某个时间线条目给出替换候选。
- `rank_takes`：[src/utils/take_ranking.py][r-rank] 的模块注释说只衡量流畅度（口头禅密度、重启、无卡顿时长、脚本覆盖率），"never a 'best take'"，因为"emotion is roughly half the decision"。
- `plan_report`：把计划渲染成 Markdown，列出改了什么、为什么改、刻意没动什么、哪些没法检查、哪些需要人判断。注释给出的理由是："A 340-entry keep_ranges array is not reviewable."

[r-ee]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L24309-L24420
[r-rank]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/take_ranking.py#L1-L28

### A3. OpenMontage

- **[`frame_sampler`][om-fs]**：
  - `strategy` 取 `interval`、`count`、`timestamps` 或 `scene_guided`（按场景边界取，`max_frames` 默认 20）。
  - `format` 默认 jpg，`quality` 默认 2（ffmpeg `-q:v`）。
  - 返回 `{frame_count, frames, output_dir}`，**只给路径**，不拼联系表，也不缩放。
- **[`visual_qa`][om-vqa]**：
  - `operation` 取 `review`（按时间戳抽帧）、`probe`（分辨率、时长、音频、像素格式、文件大小，可以对照 `expected`）或 `audio_levels`。
  - 模块说明写的是 "Returns frame paths so the agent can visually inspect them"。
- **成片自检**（[tools/video/video_compose.py#L2389-L2470][om-vc]）：
  - 在时长的 10%、35%、65%、90% 处各抽一帧；
  - PNG 小于 2000 字节就判为黑帧；
  - `volumedetect` 均值 < -60 dB 判为静音，峰值 > -0.5 dB 判为削波；
  - 另外用 ffprobe 检查时长漂移：超出 `edit_decisions` 目标时长 25% 即报问题。
- **Agent 目检流程**（[skills/pipelines/explainer/compose-director.md#L295-L365][om-cd]）：
  - 6a：ffprobe 检查。没有音频流就停下修复，不许把成片交给用户。
  - 6b：用 `frame_sampler` 在**每个 cut 的中点**抽帧。
  - 6c：用 Whisper 转写成片。0 个词说明音频缺失；少于脚本词数的 80% 说明被截断。
  - 6d：逐帧目检背景、图片是否被拉伸或空白、字幕、叠加层、片头、CTA 文字。
  - 6e/6f：汇总成固定格式的报告，交给用户。
  - 图像由宿主 Agent（Claude Code 等）用自己的读文件工具读取。

[om-fs]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/analysis/frame_sampler.py#L28-L140
[om-vqa]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/analysis/visual_qa.py#L1-L110
[om-vc]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/video/video_compose.py#L2389-L2470
[om-cd]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/skills/pipelines/explainer/compose-director.md#L295-L365

### A4. NarratoAI、VideoDB Director（"VLM 转文字"路线）

- **NarratoAI**：
  - 抽帧：ffmpeg `fps=1/{interval}`，输出 jpg，`-q:v 2`，**不缩放**。见 [app/utils/video_processor.py#L205-L230][n-vp]。
  - 默认参数：间隔 3 秒（`frame_interval_input`），每批 10 帧（`vision_batch_size`），并发 2（`vision_max_concurrency`）。见 [frame_analysis_service.py#L253-L281][n-fa]。
  - VLM 提示词要求输出 `analysis[{timestamp, picture, scene_type, key_elements, visual_quality}]`，并写明"严禁虚构不存在的内容"。见 [prompts/documentary/frame_analysis.py][n-prompt]。
  - 文字结果进入后续的解说生成；主模型看不到图像。
- **Director**：
  - `index` agent 默认按镜头切分（`threshold 20`、`min_scene_len 15`），**每个镜头取 4 帧**；也可以按时间切分（每 10 秒取首、中、尾三帧）。由 VideoDB 服务端的 VLM 生成场景描述。见 [agents/index.py#L10-L20][d-idx]。
  - `frame` agent 抽出的帧**只推给前端展示**，返回给推理引擎的是文字 "Frame extracted and displayed to user."。见 [agents/frame.py#L36-L72][d-frame]。

[n-vp]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/utils/video_processor.py#L205-L230
[n-fa]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/documentary/frame_analysis_service.py#L253-L281
[n-prompt]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/prompts/documentary/frame_analysis.py
[d-idx]: https://github.com/video-db/Director/blob/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f/backend/director/agents/index.py#L10-L20
[d-frame]: https://github.com/video-db/Director/blob/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f/backend/director/agents/frame.py#L36-L72

### A5. Claude 端的约束（决定联系表该多大）

来源：[Claude Vision 文档](https://platform.claude.com/docs/en/build-with-claude/vision)。

- 图像成本为 `⌈w/28⌉ × ⌈h/28⌉` 个视觉 token。
- 高分辨率档（Claude 4.7 及以后）：长边 2576 px，最多 4784 token。标准档：长边 1568 px，最多 1568 token。超出的图片会被等比缩小。
  - 举例：1920×1080 在高分辨率档不缩放，约 2691 token。
- **一次请求里超过 20 张图时，每张图适用更严的尺寸上限**。计数包括历史轮次重发的图，**也包括嵌在 `tool_result` 里的图**。超限的图直接被拒绝（`invalid_request_error`）。文档建议每边不超过 2000 px，或者把图片数量控制在 20 张以内。
  - 这对长会话里反复看联系表的 Agent 是硬约束：历史上的联系表会一直计入。
- 多轮对话中历史图片每轮都会随上下文重发。文档建议用 Files API 的 `file_id` 引用来控制请求体积。

**ArcReel 侧现状**：

- 内嵌 adapter [server/agent_toolset/embedded.py#L33](../../server/agent_toolset/embedded.py) 只构造 `TextContent`。
- 但当前使用的 `claude_agent_sdk` 0.2.139 在 in-process MCP 桥里会原样转发 `type:"image"` 条目（`{type, data, mimeType}`），见 `.venv/.../claude_agent_sdk/_internal/query.py` 约 L704。
- 因此，**只需要让 adapter 支持输出图像块**，就能把联系表内联给模型，不需要 Agent 在 sandbox 内读文件。

## 三、Part B：时间线管理

### B1. DaVinci Resolve MCP（最完整）

**工具面**：compound 模式下是 `tool(action, params)`。

| 能力 | 工具.动作 | 行为与闸门 |
|---|---|---|
| 列表 / 当前 | `timeline.list`、`timeline.get_current` | 只读，不触发版本 |
| 创建 | `media_pool.create_timeline(name, if_exists?)`、`create_timeline_from_clips(name, clip_ids \| clip_infos)`、`import_timeline` | 见 [L21217-L21231][r-mp] |
| 复制 | `timeline.duplicate(name?)`，默认名为 `<name> Copy` | [L25969-L25971][r-dup] |
| 改名 | `timeline.set_name(name)` | 版本归档时**不改工作时间线的名字**，因为 Resolve 改名会丢失播放头和界面状态 |
| 变体 | `timeline.create_variant_from_ranges(name, ranges, markers?, cdl?, dry_run?)`；`edit_engine.execute_*` | 用**素材帧**范围组装一条新时间线，原时间线不动 |
| 版本 | `timeline_versioning.{begin_run, end_run, list_runs, archive_current, list_versions, diff_versions, diff_timelines, get_history, rollback, prune}` | [L23808-L23950][r-tv] |
| 删除 | `media_pool.delete_timelines(timeline_ids)`；`timeline.delete_track`；`timeline.delete_clips(ripple=True)` | confirm token 加严格归档 |

[r-mp]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L21198-L21260
[r-dup]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L25960-L25990
[r-tv]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L23808-L23950

**改前归档（version-on-mutate）**：实现见 [src/utils/timeline_versioning.py][r-tvm] 和 [src/utils/destructive_hook.py][r-dh]。

- 所有登记为破坏性的 `(tool, action)` 都会被装饰器包住。执行前把当前时间线复制一份，命名为 `<name>_archived_vNN`，移进 `Archive` 素材箱，并在 SQLite 表 `timeline_versions` 里记一行。
- `begin_run` 之后，同一个 `analysis_run_id` 内的多次破坏性调用**只归档一次**。服务器指令原话："produce ONE archived predecessor, not N"。
- 可以传 `metric`、`direction`、`rationale`，hook 会自动采集改前改后的指标值（时长、片段数、空隙数等），写入 `brain_edits` 表，作为日后调优的依据。
- 归档失败时默认**不阻塞**编辑，只记警告。对 `delete_timelines`、`delete_track`、`delete_clips(ripple=True)` 这类灾难性操作，默认开启 strict：无法归档就拒绝执行。
- `prune(keep_n=10)`：超出保留数量的旧版本导出为 `.drt` 文件后从素材箱删除；数据库行保留，回滚时可以重新导入。

[r-tvm]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/timeline_versioning.py
[r-dh]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/destructive_hook.py

**回滚 `rollback_to_version`**（[timeline_versioning.py#L562-L635][r-rb]）：

1. 先归档当前工作状态。回滚本身也是一次带版本的操作。
2. 找到目标归档；如果已经被压缩成 `.drt`，就重新导入。
3. 复制成 `<name>_rolled_back_<HHMMSS>` 并设为当前时间线，**不覆盖原名**。需要的话，由调用方再改名。

[r-rb]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/timeline_versioning.py#L542-L640

**confirm token 闸门**（[src/utils/confirm_tokens.py][r-ct]，受闸门保护的动作集合见 [server.py#L1326-L1377][r-gated]）：

- 首次调用**不执行**，返回：

  ```json
  {"status": "confirmation_required", "code": "CONFIRMATION_REQUIRED",
   "category": "pending_user_decision", "confirm_token": "...",
   "preview": {...}, "ttl_seconds": 300}
  ```

  以 `delete_timelines` 为例，`preview` 列出 `timelines_lost` 数量和前 25 个时间线名。
- 带上 token 重新调用才执行。token 的约束：
  - 一次性；
  - 5 分钟过期（注释："long enough for a human to read a preview... short enough that a token left lying around in a transcript is not a standing authorisation"）；
  - 绑定动作名；
  - 绑定 `(action, params)` 的 SHA-256 指纹，参数一变 token 就失效；
  - 只在签发它的进程内有效。
- 受闸门保护的动作：`delete_track`、`apply_cuts`、`ripple_insert`、`media_pool.delete_clips/delete_folders/delete_timelines`、全部 `edit_engine.execute_*`、调色整图覆盖、生成类 AI 操作（因为"expensive and irreversible without manual cleanup"）。`timeline.delete_clips` 只在 `ripple=True` 时受闸门保护。
- 闸门可以用偏好 `destructive.require_confirm_token` 关闭，默认开启。
- 签发 token 的那次调用**不触发归档**，避免浪费一个版本号。
- 另有 safe mode：拒绝风险等级为 HIGH 或 CRITICAL 的操作。

[r-ct]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/confirm_tokens.py
[r-gated]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L1320-L1380

**变体优先**：`execute_tighten` 的实现见 [server.py#L25006-L25100][r-et]。

- 预览里明确写着 "Assembles a tightened VARIANT timeline ... the original timeline is not modified"；如果变体没有音频，还会显式警告 "VIDEO-ONLY (silent)"。
- 执行后返回改前改后的指标，以及一个紧凑的 `structural_diff`。完整差异保存在计划记录里，只有传 `include_details=true` 时才内联返回。
- 唯一原地修改的是 `execute_swap`（同位替换），执行前同样会先归档。

[r-et]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L25006-L25100

**返回信封**：[src/utils/operation_result.py][r-op] 在默认 `dual` 模式下原样保留领域负载，另加一个 `_operation` 键，回答三个问题：做了没有、验证了没有、改了什么。

[r-op]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/operation_result.py

**渲染**：`render` 工具，见 [server.py#L20713-L20790][r-render]。

- 基本动作：`add_job` → `start(job_ids?)` → `get_job_status` / `is_rendering`。
- `prepare_render_job`、`safe_set_render_settings`、`safe_quick_export` 都支持 `dry_run`；`safe_quick_export` 还需要 `allow_render`。
- `verify_output(job_id, expected_frames?, expected_duration_seconds?)` 用 ffprobe 检查真实输出文件。原因是 JobStatus 会对几乎为空的产物也报 Complete；文档要求在删除任务之前先做验证。
- 通用长任务用 `_run_maybe_background`（[L1786-L1800][r-bg]）：传 `background=true` 时返回 `{job_id, status:"running"}`，再用 `resolve_control(action="job_status")` 轮询。

[r-render]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L20713-L20790
[r-bg]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/server.py#L1786-L1800

### B2. 其他实现

| 实现 | 创建 | 复制 / 变体 | 改名 | 列表 | 版本与回滚 | 删除 | 渲染触发 |
|---|---|---|---|---|---|---|---|
| **Diffusion Studio** | Agent 直接写 JSX 文件；`open` 打开项目文件夹 | 无工具（复制文件） | 无 | `context` 读应用状态 | 工具面无历史或撤销（源码里未见 checkpoint 或 undo 实现） | 无工具 | `export(id, path?)` **同步阻塞**，最多 60 分钟，同一时间只允许一个导出；设置来自项目 `package.json`，不在调用参数里；skill 规定"Only render (export) the result when prompted" |
| **OpenMontage** | 宿主 LLM 整份写 `edit_decisions` | 无 | 无 | 读检查点 | `write_checkpoint` 覆盖前把旧检查点复制进 `history/`（进行中的心跳不归档），见 [lib/checkpoint.py#L349-L386][om-ck]；没有回滚工具 | 无 | `video_compose` 标记为 `ExecutionMode.SYNC`；渲染后强制自检 |
| **NarratoAI** | 生成或编辑脚本后保存为 `{YYYY-MMDD-HHMMSS}.json`，见 [script_settings.py#L1918-L1932][n-save] | 每次保存都是新文件 | 无 | glob `*.json` 按创建时间倒序，见 [L440-L455][n-list] | 靠文件累积，没有回滚操作 | 无 | `start_subclip(task_id, ...)` 在线程里执行，`state.update_task` 更新进度供 WebUI 轮询 |
| **Director** | LLM 生成 Python 代码构造 VideoDB `Timeline` | 每轮整份重写 | 无 | 无 | 无 | 无 | 代码里调用 `timeline.generate_stream()` **同步**返回 HLS 流地址（云端即时拼接，不走渲染队列），见 [editing/agent.py#L661-L710][d-ed] |
| **VectCutAPI** | `create_draft` | 无 | 无 | HTTP 有 `/list_projects`，MCP 未暴露 | 无 | 无 | MCP `save_draft(draft_id)`；HTTP 侧 `/save_draft` 后台执行，用 `/query_draft_status` 轮询，见 [mcp_server.py#L41-L258][vc-mcp]、[capcut_server.py#L789-L950][vc-srv] |
| **jianying-mcp** | `create_draft(draft_name, width, height, fps)`、`create_track` | 无 | 无 | 无 | 无 | 无；只有 `add_*`，没有删除片段的工具 | `export_draft(draft_id, jianying_draft_path)` 写入本地剪映草稿目录，见 [draft_tool.py][jy-draft] |
| **Descript Underlord** | 对话 | — | — | 应用内历史聊天列表 | 每条回复的操作栏有 **Revert**；重新打开旧聊天后 Revert 仍可用；另有编辑器撤销（[帮助页](https://help.descript.com/hc/en-us/articles/36958274409357-Revert-or-rollback-changes-made-by-Underlord-beta)） | — | API：`POST /v1/jobs/agent {project_id?, project_name?, composition_id?, model?, prompt, callback_url?}` 立即返回 `job_id`；`GET /v1/jobs/{job_id}` 状态为 `running`/`stopped`，结果含 `agent_response`、`project_changed`、`ai_credits_used`；发布用 `POST /v1/jobs/publish`（[API 文档](https://docs.descriptapi.com/)） |
| **Shotstack MCP** | `render_video` 提交整份 Edit JSON | 模板：`create_template`、`render_template` | — | `list_templates` | 无 | `delete_template`（文档未提闸门） | 默认 `studio`（人在 iframe 里点渲染）；`render_video` 加 `get_render_status` 轮询 `queued/fetching/rendering/saving/done/failed`（[MCP 文档](https://shotstack.io/docs/guide/agents/mcp-server.md)） |

[om-ck]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py#L349-L386
[n-save]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/webui/components/script_settings.py#L1918-L1932
[n-list]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/webui/components/script_settings.py#L440-L455
[d-ed]: https://github.com/video-db/Director/blob/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f/backend/director/agents/editing/agent.py#L661-L710
[vc-mcp]: https://github.com/sun-guannan/VectCutAPI/blob/7e5b9c407faa933163b867711a793f343d22bb04/mcp_server.py#L41-L258
[vc-srv]: https://github.com/sun-guannan/VectCutAPI/blob/7e5b9c407faa933163b867711a793f343d22bb04/capcut_server.py#L789-L950
[jy-draft]: https://github.com/hey-jian-wei/jianying-mcp/blob/f312beac6091c9580e85edb4094878916b7b983d/jianyingdraft/tool/draft_tool.py

**横向观察：**

- **没有任何实现把"删除时间线"不加闸门地开放给 Agent。**Resolve 有删除工具但用 confirm token 保护；其余实现干脆不提供删除工具。
- **回滚在所有实现中都是追加式的。**Resolve 回滚产生新时间线并先归档当前状态；Descript 的 Revert 是对话粒度的。**没有实现在回滚时就地抹掉历史。**
- **"变体"与"修订"是两种不同的东西。**Resolve 把自动化的大改动（精选、压缩、去静音）输出成**新名字的时间线**，把人工的小改动留在原时间线上，靠归档版本兜底。

## 四、Implications for ArcReel

前提：剪辑时间线按集组织，有名字，一集可以有多条；每次写入追加一个不可变修订；回滚等于用旧修订生成一个新修订；内嵌 MCP adapter 目前只返回文本。

### 1. L2 看素材：工具形状与图像交付

**建议两个只读工具，把"便宜的确定性信号"和"花 token 的图像"拆开。**

- **`check_timeline(timeline, revision?)`**：不渲染、不调用模型，参照 Diffusion `check` 加 Resolve 的 readthrough。
  - 结构问题：入出点越界、空隙或黑场（没有排任何画面）、引用的视频单元版本缺失或生成失败、旁白超出所在 cut、字幕与 cut 不对齐。
  - 按视频单元版本**缓存**的 ffmpeg 信号：`blackdetect`（Resolve 的参数 `d=0.5:pix_th=0.10` 可作起点）、`silencedetect`、场景分数（用自适应阈值，不要写死 0.3），另外补上调研对象都没做的 `freezedetect`。生成式视频常见的坏帧是开头或结尾冻结、黑场和闪帧，比拍摄素材更需要这一项。
  - 返回紧凑 JSON：`issues[{code, severity, cut_id, unit_id, version, ranges}]`。
- **`inspect_frames(target, times | count | per_cut, per_sheet?)`**：
  - `target` 取两种：
    - 一个视频单元版本：看素材本身的像素，对应 Diffusion `media_grab`，用于挑版本、找坏帧；
    - 时间线的一个修订加时间范围：看合成后的画面，包括字幕和转场，对应 `capture`，用于验证剪辑结果。
  - 服务端用 ffmpeg 拼联系表：每张最多 12 格，每格烧录时间码加 `cut_id` 或 `unit@version` 标签。
  - **整张长边默认不超过 2000 px**，不用 2576。原因是长会话里 tool_result 图片累计超过 20 张后，超过 2000 px 的图会被 API 拒绝。2000×1125 的联系表约 72×41 ≈ 2950 token。
  - 采样模式照搬 Diffusion：`times`（Agent 已经知道看哪里，例如 `check` 报出的范围）、`count`（均匀取），以及 `per_cut: head|mid|tail`（对应 OpenMontage 取 cut 中点的做法）。
  - 每次调用有帧数硬上限（例如 24，即两张联系表），并参照 Resolve analysis_caps 做每个会话的图像 token 预算，超出就在调用前拒绝。
- **交付方式：扩展内嵌 adapter，支持返回 MCP `ImageContent`。**
  - 条件：图像数和字节数不超过阈值（参照 Diffusion：≤4 张、≤1 MB）。同时附一个 text 块，写明每格与 `(cut_id, unit_id, version, source_time)` 的对应关系，便于 Agent 引用。
  - SDK 桥已经支持 image 条目，改动只在 adapter。
  - **不采用** Resolve 的 `host_chat_paths`（返回路径让 Agent 读文件）。它要求 Agent 在 sandbox 里对产物目录有读权限，还要多一次往返。
  - 联系表同时落盘作为产物，前端可以展示"Agent 看了什么"。
- **结果怎么进入决策**：
  - 看完后的结论（例如"v2 第 1.2 秒前冻结，建议入点从 1.25 秒开始"）通过写时间线的操作工具落地，**理由写进 cut 的 `reason` 字段**，而不是另设 `commit_vision` 那样的回写协议。
  - 挑版本只给排序和证据，不宣称"最好"，参照 Resolve `rank_takes` 的表述。
  - 触发重生成仍然只能提议，由用户确认。
  - 全集按画面内容检索（"找到有雨的镜头"）才需要"VLM 转文字缓存"（NarratoAI 或 Descript 路线），可以晚做。

### 2. 时间线管理工具集与破坏性操作闸门

建议的工具（命名只是示意）：

| 工具 | 语义 | 闸门 |
|---|---|---|
| `list_timelines(episode)` | 名字、最新修订号、更新时间、时长、是否已归档 | 只读 |
| `get_timeline(name, revision?)` | 紧凑表：cut id、单元与版本、入出点、转场、旁白、字幕、BGM | 只读 |
| `create_timeline(episode, name, from)` | `from` 取 `script`（按剧本单元自动首剪）、`timeline:<name>@<rev>`（复制或变体）或 `empty` | 无；新对象不破坏任何东西 |
| `rename_timeline(name, new_name)` | 只改元数据，名字唯一性由服务端校验 | 无 |
| `list_revisions(name)` / `diff_revisions(name, a, b)` | 每个修订带 `author`（agent 或 user）、`turn_id`、摘要 | 只读 |
| `restore_revision(name, revision)` | 以旧修订内容**追加**一个新修订 | 无需确认：天然可逆，这一点与 Resolve 回滚"先归档再恢复"的语义一致 |
| 编辑操作（裁切、移动、换版本、转场、音轨……） | 每次调用追加一个修订，返回类似 `_operation` 的信封：`{revision, changes, verification}` | 无；修订不可变就是兜底 |
| `archive_timeline(name)` | 软删除：从列表隐藏，修订保留，可恢复 | 无或轻量确认 |
| 硬删除 | **不暴露给 Agent**，只在 UI 提供 | — |

补充建议：

- **按 Agent 回合聚合修订。**每条修订记录 `turn_id`（对应 Resolve 的 `analysis_run_id`），前端就能按回合提供 Descript 式的"撤销这一轮"：直接 `restore_revision` 到本回合之前的修订。实现时也可以选择一个回合只提交一个修订。
- **大改动产出新时间线，小改动产出新修订。**"自动精简""按新节奏重排"这类批量改动，参照 Resolve `execute_*`，用 `create_timeline(from=timeline:X@rev)` 生成一条新命名的时间线，让用户并排比较；对话里的小改动（"第 3 镜短 1 秒"）直接在原时间线上追加修订。
- **什么时候才需要 confirm token。**因为修订不可变、软删除可恢复，ArcReel 的时间线管理里没有真正不可逆的操作，**不需要**引入 confirm token。只有将来开放不可逆或高成本动作（硬删除、批量重生成、渲染成片）时，才照搬 Resolve 的实现：首次调用返回 `preview` 加 token；token 一次性、TTL 约 5 分钟、绑定动作名与参数指纹；签发 token 的调用不产生副作用。

### 3. 渲染与导出的触发

- **异步任务加轮询，复用现有的生成队列。**
  - `render_timeline(name, revision, preset)` 把修订号**钉死**，返回 `job_id`；`get_render_status(job_id)` 返回 `queued/running/done/failed`、进度和产物。
  - 队列已有"内嵌工具等待批次终态"的机制（`batch_enqueue_and_wait`），短片可以沿用等待语义，但对外契约仍应是任务。同步阻塞（例如 Diffusion 最多 60 分钟）不适合服务端多用户场景。
- **默认不由 Agent 主动渲染。**
  - skill 与工具描述都写明：只在用户明确要求时调用（参照 Diffusion editor skill 和 Shotstack 规则）。
  - 视觉确认用 `inspect_frames` 查看时间线修订，不用出成片。
  - 前端的只读预览播放器承担 Shotstack `studio` 的角色：由人点击渲染。
- **渲染前闸门是确定性的，不靠模型。**`render_timeline` 在服务端先跑 `check_timeline`，有 error 级问题就拒绝并返回问题列表。
- **渲染后必须验证。**参照 OpenMontage 自检与 Resolve `verify_output`：
  - ffprobe 检查视频流和音频流是否存在；
  - 时长与时间线计算值的偏差；
  - 对成片跑 `blackdetect` 和 `silencedetect`；
  - 把结果附在任务结果里，失败的任务不作为可交付产物展示。
- **剪映草稿导出走同一套任务接口。**以 `format` 区分成片和草稿，同样钉住修订号。剪映系 MCP 的 `save_draft`/`export_draft` 本身就是导出动作，不承担时间线管理。

## 五、未核实与局限

- Descript 帮助页在调研时无法直接抓取，Revert 的细节取自搜索摘要和前一篇调研，未能逐字核对原文。
- Diffusion Studio 的"没有历史或撤销"是根据工具文档和 `apps/desktop`、`packages/agent-chat` 源码检索得出的结论；应用可能依赖外部 git 或文件系统，未核实。
- Resolve MCP 的 confirm token、归档、变体都依赖 Resolve Studio 真机运行，本文只读了源码，没有实测。
- Claude"超过 20 张图就收紧尺寸"的阈值以文档为准；Agent SDK 会话里压缩或截断历史后怎么计数，未实测。
- 各实现的 ffmpeg 阈值（blackdetect、silencedetect、场景分数）对 AI 生成视频是否合适，没有评估。
