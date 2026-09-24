# 成片渲染后端在各部署形态下的可行性

> 状态：调研完成，结论交由地图 [#2667](https://github.com/ArcReel/ArcReel/issues/2667)「Wayfinder: Agent 自动剪辑」汇总。
> 关联：[#2672](https://github.com/ArcReel/ArcReel/issues/2672)（本票）。
> 数据日期：2026-09-24。版本号、wheel 列表和体积来自 PyPI JSON API（`https://pypi.org/pypi/<pkg>/json`）和 npm registry。许可证来自各项目的 LICENSE 文件或官方页面。能力数据中，标「实测」的是在本机（Apple M3，macOS arm64）或 Docker（`python:3.12-slim` arm64，8 vCPU / 12 GB）里运行 `-buildconf`、`-filters`、`-encoders` 或实际渲染得到的，其余来自官方文档或源码。标「推断」的是本文的推理，不是来源原文。

## 结论

1. **渲染引擎沿用服务端 ffmpeg filtergraph（以子进程方式调用 CLI）。** 本文要求的四项能力（入出点裁切、转场、多轨混音与音量、字幕烧录与样式）它都能在一张 `filter_complex` 里完成。实测速度约为实时的 4 倍，是 MoviePy 的约 7 倍。代价是内存会随输入片段数线性增长，所以需要**分段渲染**，把每段的片段数控制在上限以内。
2. **ffmpeg 可执行文件按以下顺序查找：显式配置 → PATH → `imageio-ffmpeg` wheel 自带的二进制。**
   - `imageio-ffmpeg` 是唯一一个 pip 安装即可带上完整 ffmpeg CLI、离线可用、并且包含 libx264、libass 和 xfade 的方案。
   - 它覆盖 macOS arm64/x64、Windows x64、Linux x86_64/aarch64，单个 wheel 21–31 MB。
   - 它的缺点有三：不带 ffprobe；二进制版本停在 FFmpeg 7.0/7.1（2025-01 以后没有发版）；构建是 GPL 的。
3. **命中 ffmpeg 不等于具备字幕能力，必须探测能力。** Homebrew 默认的 `ffmpeg` formula 不含 libass 和 freetype，所以没有 `subtitles`、`ass` 和 `drawtext` 滤镜（实测）。
4. **ffprobe 缺失时用 PyAV 做进程内探测。** PyAV 的 wheel 覆盖面最广（含 Windows arm64 和 musl），但它不带 libass 和 freetype，不能作为烧录字幕的主引擎。
5. **CJK 字体必须随应用提供。** Docker 镜像里只有 DejaVu 字体（实测），本地安装的系统字体也不可靠。需要自带一款 OFL 许可的中文字体，并通过 `fontsdir` 传给 libass。
6. **以下候选排除：**
   - MoviePy：与本仓库的 `Pillow>=12.1.1` 冲突，而且慢。
   - MLT、GES：无法通过 pip 在三大平台落地。
   - Remotion：公司许可证与 AGPL 自托管场景冲突。
   - 浏览器端 WebCodecs 和 ffmpeg.wasm：可作为远期选项，不作为本期降级路径。
7. **降级路径：**
   - 缺少字幕滤镜时，用 Pillow 预渲染字幕位图，再经 `overlay` 叠加，或者改为输出软字幕。
   - 缺少 xfade 时，转场退化为硬切。
   - 缺少 libx264 时，换用其他 H.264 编码器。
   - 完全没有 ffmpeg 时，不提供成片渲染，退回剪映草稿导出、素材包下载和前端时间线预览。

## 1. 现状核实（仓库）

- **Docker 镜像：**
  - `Dockerfile` 的生产阶段基于 `python:3.12-slim`（实测是 Debian 13.7），通过 `apt-get install --no-install-recommends ffmpeg` 安装 ffmpeg。
  - 实测装到的是 **FFmpeg 7.1.5-0+deb13u1**。构建参数包含 `--enable-gpl --enable-libx264 --enable-libx265 --enable-libass --enable-libfreetype --enable-libfontconfig --enable-libharfbuzz --enable-libfribidi`。
  - 实测可用的滤镜：`xfade`、`acrossfade`、`amix`、`loudnorm`、`sidechaincompress`、`subtitles`、`ass`、`drawtext`。编码器 `libx264` 和 `aac` 可用。
  - 这一层 apt 共新装 202 个包，占 **430 MB** 磁盘空间。
- **Docker 镜像的字体：** 实测 `fc-list` 只有 8 个 DejaVu 字体，没有任何 CJK 字体，所以烧录中文字幕会显示成方块。
  - Debian 的 `fonts-noto-cjk` 安装后占 91 MB。`fonts-wqy-microhei` 占 5 MB，但它是 GPL 字体。
  - 仓库里没有任何字体文件（`git ls-files` 中找不到 `.ttf`、`.otf` 或 `.woff`）。
- **本地安装：**
  - `CONTRIBUTING.md` 的前置要求写的是「Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg」，但依赖清单 `pyproject.toml` 里没有任何 ffmpeg 分发包。
  - 运行时代码在 ffmpeg 或 ffprobe 缺失时已经会降级，靠 `lib/infra/thumbnail.py` 的 `_ffmpeg_available` / `_ffprobe_available` 和 `lib/speech/audio_utils.py` 的 `_ffprobe_available` 判断，两者都用 `shutil.which` 在 PATH 里查找。
- **`compose-video` skill（`agent_runtime_profile/.claude/skills/compose-video/scripts/compose_video.py`）的实现：**
  - `resolve_ffmpeg_tools` 先查 PATH，再查各平台的常见安装目录，**必须 ffmpeg 和 ffprobe 同时存在**。安装提示是 `brew install ffmpeg` 或 `winget install Gyan.FFmpeg`。
  - 管线分多次编码：
    1. `normalize_clip` 把每个片段用 libx264 重编码一遍；
    2. `concatenate_with_transitions` 用 `xfade` 加 `acrossfade`，在 cut 边界用 `concat`，然后再编码一次；
    3. `add_background_music` 做 `volume=0.3` 加 `amix`，第三次编码。
  - 目前没有入出点裁切，也没有字幕。
- **项目许可证：** `LICENSE` 是 **GNU AGPL v3**。

## 2. 渲染引擎候选

### 2.1 服务端 ffmpeg filtergraph（CLI 子进程）

- **能力：**
  - 裁切：用 `trim`/`atrim` 加 `setpts`，或者用输入级的 `-ss`/`-t`。
  - 转场：`xfade` 内置 fade、dissolve、wipe*、slide* 等几十种。要求输入是恒定帧率，所以每路输入要先经过 `fps=…,format=yuv420p` 统一帧率和像素格式（实测：没有这一步，ffmpeg 会报错 `The inputs needs to be a constant frame rate`）。
  - 混音：`volume`（支持按时间表达式调节）、`amix`、`acrossfade`、`afade`，用 `sidechaincompress` 做闪避（ducking），用 `loudnorm` 做响度归一。
  - 字幕：`subtitles` 和 `ass` 滤镜基于 libass，ASS 样式支持字体、字号、描边、阴影、边距和对齐；`fontsdir=` 可以指定字体目录。另有 `drawtext` 可用。
  - 来源：[FFmpeg Filters 文档](https://ffmpeg.org/ffmpeg-filters.html)。
- **速度与资源（实测，Docker 8 vCPU，FFmpeg 7.1.5）：**
  - 测试内容：6 段 1080×1920、24fps 的片段，每段裁到第 1–9 秒；5 处 0.5 秒的 `xfade`/`acrossfade`；BGM 用 `volume=0.3` 加 `amix`；外加 ASS 字幕烧录。单次 `filter_complex` 输出 45.5 秒的成片。
  - `libx264 -preset veryfast -crf 20` 耗时 **11.2 s**（约实时的 4 倍）；`-preset medium` 耗时 21.5 s。
  - **内存峰值随输入数线性增长**，只串 xfade、无字幕（`scale.py`）时测得：

    | 输入片段数 | 峰值内存 | 耗时 |
    |---|---|---|
    | 3 | 1.3 GB | 8.8 s |
    | 6 | 2.3 GB | 18.4 s |
    | 12 | 4.3 GB | 35.0 s |
    | 24 | 7.5 GB | 77.1 s |

  - 这个增长幅度在一集几十个单元的规模下不可接受。结论是需要分段：每段不超过 N 个片段，单独渲染，段与段之间在硬切边界用 concat demuxer 加 `-c copy` 拼接，或者只对拼接缝附近重新编码。「推断」：内存增长的原因是 ffmpeg 并行解码所有输入，并为后续的 xfade 输入缓存帧队列。
- **依赖体积与许可证：** 见 §3，取决于从哪里获得二进制。以子进程方式调用属于 [GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation) 所说的「管道 / 命令行参数通信」，不构成链接。
- **适配度：** 与现有 `compose-video` 同构，现有的 xfade 分组和短片段降级逻辑可以复用。可以把三次编码合并成一次，或者合并成「分段一次编码 + 无损拼接」。

### 2.2 PyAV（`av`）

- **版本与平台：** av 18.1.0（2026-08-12），BSD-3-Clause，Python ≥3.11，使用 abi3 wheel。
  - 平台：macOS arm64/x86_64、manylinux x86_64/aarch64/armv7l、**musllinux** x86_64/aarch64、Windows amd64/**arm64**。
  - 单个 wheel 18–39 MB。
  - 来源：[PyPI](https://pypi.org/pypi/av/json)、[PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV)。
- **自带 FFmpeg 8.1.2（实测）：**
  - 构建参数 `--disable-programs`，所以**没有 ffmpeg 或 ffprobe 可执行文件**。
  - 包含 libx264、libx265 和 VideoToolbox。**不含** libass、freetype 和 fontconfig。
  - 滤镜：`xfade`、`amix`、`acrossfade`、`volume`、`overlay`、`concat`、`trim` 可用。**`subtitles`、`ass`、`drawtext` 都不可用。**
  - 来源：[pyav-ffmpeg](https://github.com/PyAV-Org/pyav-ffmpeg)。
- **能力：** `av.filter.Graph` 支持多输入图（`add_buffer`/`add_abuffer`，`push(frame, at=idx)`），可以做裁切、转场和混音（[API 文档](https://pyav.basswood.io/docs/stable/api/filter.html)）。字幕只能先用 Pillow 画成位图，再通过 `overlay` 叠加。时间线调度、音画同步和 PTS 处理都要自己写。PyAV 上游 README 原话是："If the ffmpeg command does the job without you bending over backwards, PyAV is likely going to be more of a hindrance than a help."
- **许可证：**
  - pyav-ffmpeg 的 `patches/ffmpeg.patch` 把 x264/x265 从 FFmpeg 的 GPL 库列表挪到了 version3 列表，所以运行时 `avutil_license()` 报告的是 "LGPL version 3 or later"。
  - 但 x264 本身是 GPL（[x264 官网](https://www.videolan.org/developers/x264.html)）。维护者在 [PyAV#2270](https://github.com/PyAV-Org/PyAV/issues/2270) 中的说法是 "LGPL + Commercial exceptions OR GPL"。
  - 「推断」：在没有购买 x264 商业授权时应按 GPL 对待。由于是进程内链接，这属于 GPL 组合，但与 AGPLv3 兼容。
- **适配度：** 不适合作为主渲染引擎，因为缺少 libass。适合作为**进程内探测工具**，在 ffprobe 缺失时读取时长、帧率和流信息，也可以作为 overlay 字幕降级路径的执行器。

### 2.3 MoviePy

- **版本、许可与依赖：** 最新版 2.2.1（2025-05-21），MIT。README 写着 "Maintainers wanted!"。
  - `requires_dist` 包含 **`pillow<12.0,>=9.2.0`** 和 `imageio_ffmpeg>=0.2.0`（[PyPI](https://pypi.org/pypi/moviepy/json)）。
  - **本仓库 `pyproject.toml` 要求 `Pillow>=12.1.1`，两者直接冲突，装不进来。**
- **引擎：** 先用 Python/NumPy 逐帧合成，再把帧通过管道交给 imageio-ffmpeg 的 ffmpeg 编码。v2 起文字改由 Pillow 渲染（[v2 迁移说明](https://zulko.github.io/moviepy/getting_started/updating_to_v2.html)）。
- **能力：**
  - 裁切：`subclipped`。
  - 转场：`vfx.CrossFadeIn/Out`、`FadeIn/Out`、`SlideIn/Out`，没有 wipe。
  - 混音：`CompositeAudioClip` 加 `afx.MultiplyVolume`，没有现成的 ducking。
  - 字幕：`TextClip` 基于 Pillow，支持描边和对齐，需要提供字体文件路径，不能直接使用 ASS 样式。
- **速度（实测，同一 Docker 环境，pip 装到的是 2.1.2）：** 在同样的 6 段裁切、xfade 和 BGM 场景下（**不含字幕**），`preset=veryfast` 耗时 **77.0 s** 和 72.5 s（两次运行），约为 ffmpeg filtergraph 的 7 倍慢，峰值内存约 450 MB。上游 [#2395](https://github.com/Zulko/moviepy/issues/2395) 报告 v2 比 v1 慢 10 倍。
- **结论：** 排除。

### 2.4 MLT / melt

- **版本与许可：** v7.40.0（2026-06-25），核心为 LGPL-2.1，movit、plusgpl、qt、rubberband、vid.stab 等模块含 GPL 代码（[安装文档](https://www.mltframework.org/docs/install/)）。Shotcut、Kdenlive 和 Flowblade 都基于它。
- **能力：** 很完整。
  - 转场：`luma` 做叠化和擦除，`mix` 做音频交叉淡化（[转场插件](https://www.mltframework.org/plugins/PluginsTransitions/)）。
  - 滤镜：`volume`、`loudness`、`avfilter.ass`、`qtext`、`dynamictext`（[滤镜插件](https://www.mltframework.org/plugins/PluginsFilters/)）。
  - 时间线可以用 MLT XML 描述，由 `melt` CLI 渲染。
- **分发：**
  - PyPI 上没有包（`mlt`、`mlt7` 都返回 404）。Python 绑定走 SWIG，需要自行编译。
  - Windows 和 macOS 没有官方独立 SDK，只能从 Shotcut 发行包里拆出 `melt`。
  - Docker 里可以 apt 安装，但本地安装无法落地。
- **结论：** 排除。

### 2.5 GStreamer Editing Services（GES）

- **能力：** layer/track 时间线；`GESTransitionClip` 提供叠化和 SMPTE 擦除（音频只有 crossfade）；`GESTextOverlayClip` 支持 Pango 字体描述、颜色和位置（[GES 文档](https://gstreamer.freedesktop.org/documentation/gst-editing-services/index.html)）。
- **分发：**
  - GStreamer 1.28 起有官方 pip wheel（`gstreamer-bundle`），但**只有 macOS universal2 和 Windows**，Linux 的 manylinux wheel 还在计划中（[Centricular devlog 2026-02](https://centricular.com/devlog/2026-02/Python-Wheels/)）。
  - 体积很大：mac 上 libs 约 96 MB，plugins 约 101 MB。
  - 单独的 PyGObject 3.58.0 在 PyPI 上只有 sdist，需要自行编译。
- **许可证：** LGPL。但 `x264enc` 在 plugins-ugly 里，属于 GPL。
- **结论：** 排除。三个平台的 pip 覆盖不全，体积大，时间线语义与 ffmpeg 相比没有明显优势。

### 2.6 Remotion（Node + Chromium）

- **版本与引擎：** 4.0.527（2026-09-22）。渲染方式是 Chrome Headless Shell 逐帧截图（[并发文档](https://www.remotion.dev/docs/terminology/concurrency)），然后交给内置的精简版 FFmpeg（含 x264，属于 GPL，见 [Remotion FFmpeg 许可说明](https://www.remotion.dev/docs/miscellaneous/ffmpeg-license)）编码。
- **能力：** `@remotion/transitions` 提供 fade、wipe、slide、iris 等转场；`<Audio volume>` 可以按帧设置音量，能做 ducking；字幕用 HTML/CSS，Web 字体和 CJK 都自然支持。能力最强。
- **依赖：**
  - compositor 包 17–30 MB，各平台见 npm。
  - Chrome Headless Shell 压缩包约 99–120 MB，首次使用时下载（[文档](https://www.remotion.dev/docs/miscellaneous/chrome-headless-shell)）。
  - 需要 Node 运行时，而 ArcReel 的生产镜像只有 Python。
- **许可证（source-available，不是 OSI 开源许可）：**
  - 个人以及 ≤3 人的组织免费；**4 人及以上需要 Company License**（[LICENSE.md](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md)、[terms](https://github.com/remotion-dev/remotion/blob/main/packages/docs/docs/terms.mdx)）。
  - 以程序方式调用 `renderMedia` 的组织归入 "Remotion for Automators"，按每次渲染计费并有月度最低消费（[remotion.pro/license](https://www.remotion.pro/license)）。
  - 得到 Remotion 代码访问权的终端用户，如果达到 4 人及以上，需要自行购买许可。
  - 「推断」：ArcReel 是 AGPL 自托管软件。用户部署后会在自己的服务器上调用 `renderMedia`，4 人以上的团队因此需要各自购买许可。这与「AGPL 任何人可自由运行」的预期冲突，而且 Remotion 的许可条款与 AGPL 的再分发要求相容性存疑。
- **结论：** 排除。

### 2.7 浏览器端（WebCodecs / ffmpeg.wasm）

- **WebCodecs 支持情况：**
  - `VideoEncoder`：Chrome/Edge 94+、Firefox 130+（仅桌面版）、Safari 16.4+。
  - `AudioEncoder`：Safari 要到 **26** 才支持。
  - 必须在安全上下文（HTTPS）下使用。
  - 来源：[caniuse](https://caniuse.com/webcodecs)、MDN。
  - MDN 明确写道："AAC encoding … is not supported in Firefox on any platform, or in any browser on desktop Linux."（[Codec selection](https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API/Codec_selection)）
  - 封装可以用 Mediabunny 1.59.1（MPL-2.0；`mp4-muxer` 已废弃，由它取代）。它只负责编解码和封装，**不提供合成、转场或文字**，这些要自己用 Canvas/WebGL 实现。
- **ffmpeg.wasm：**
  - `@ffmpeg/core` 0.12.10 是 GPL-2.0-or-later，wasm 文件 32 MB，内含 x264 和 libass。
  - 官方基准：单线程比原生慢约 25 倍，多线程慢约 12 倍（[性能](https://ffmpegwasm.netlify.app/docs/performance)）。
  - 多线程版需要 COOP/COEP 响应头；单个文件有 2 GB 上限。
  - 仓库最后一次提交是 2025-09，项目已基本停滞。
- **Remotion `renderMediaOnWeb`：** 同样基于 WebCodecs 加 Mediabunny，同样受上述许可约束。
- **结论：** 渲染要在用户标签页里跑完，受浏览器兼容性和编码器差异限制，需要自建合成层，开发量最大。不作为本期降级路径，只作为远期「服务端完全不可渲染」时的候选。

## 3. 部署约束：能随 Python 依赖分发的 ffmpeg

| 方案 | 分发方式 | 平台 | FFmpeg 版本 | ffprobe | libx264 | libass（`subtitles`/`ass`） | `xfade` | `drawtext` | 许可证 | 离线可用 |
|---|---|---|---|---|---|---|---|---|---|---|
| Debian apt（Docker 镜像现状） | 系统包 | 镜像内 | 7.1.5（实测） | 有 | 有 | 有（实测） | 有 | 有 | GPL | 是 |
| **imageio-ffmpeg 0.6.0** | wheel 内嵌可执行文件，21–31 MB | macOS arm64/x64、Windows x64/x86、manylinux2014 x86_64/aarch64。**无** Windows arm64、musl | macOS/Windows 7.1，Linux 7.0.2 | **无** | 有 | 有（macOS 实测，其余平台查 buildconf 字符串） | 有 | Linux 版缺 harfbuzz，「推断」没有 drawtext | 包是 BSD-2，二进制是 GPL | 是 |
| static-ffmpeg 3.0 | 纯 Python，首次运行时从 GitHub 下载 zip | Windows x64、macOS x64/arm64、Linux x64/arm64 | 声称 v8.0，但实测 darwin_arm64 包里是 **7.0** | 有 | 有 | 有 | 有 | 有 | 包是 MIT，二进制来源和许可证都没有文档说明，已发布版本不校验哈希 | **否**（首次运行需要联网） |
| PyAV 18.1.0 | wheel 内嵌动态库，18–39 MB | 最全（含 Windows arm64、musl、armv7） | 8.1.2 | 无（进程内 API 可以替代探测） | 有 | **无** | 有 | **无** | BSD 加上「推断」的 GPL | 是 |
| ffmpeg-downloader（`ffdl`） | CLI 下载器 | 各平台 | 跟随上游 | 有 | 视来源 | 视来源 | 有 | 视来源 | GPL-2.0 | 否 |
| Homebrew `ffmpeg`（本地常见） | 用户自行安装 | macOS | 9.0.2（formula 版本） | 有 | 有 | **无**（依赖里没有 libass 和 freetype；本机 8.0.1 实测也没有） | 有 | **无** | GPL | 是 |
| Homebrew `ffmpeg-full` | 用户自行安装 | macOS | 9.0.2 | 有 | 有 | 有 | 有 | 有 | GPL | 是，但它是 keg-only，默认不在 PATH 里 |

**表中各项的来源：**
- imageio-ffmpeg：[PyPI](https://pypi.org/pypi/imageio-ffmpeg/json) 和 [仓库](https://github.com/imageio/imageio-ffmpeg) 的 `_definitions.py`。二进制来自 [imageio-binaries](https://github.com/imageio/imageio-binaries/tree/master/ffmpeg)：macOS 版来自 osxexperts，Windows 版来自 gyan.dev essentials，Linux 版来自 johnvansickle。
- static-ffmpeg：[仓库](https://github.com/zackees/static_ffmpeg) 的 `run.py`。
- Homebrew：[formula API](https://formulae.brew.sh/api/formula/ffmpeg.json) 和 [`ffmpeg-full`](https://formulae.brew.sh/api/formula/ffmpeg-full.json)。
- 其他预编译来源：[BtbN FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) 有 gpl 和 lgpl 两个变体，只有 Windows 和 Linux，**没有 macOS**；[Martin Riedl](https://ffmpeg.martin-riedl.de/) 提供 macOS arm64/amd64 和 Linux 的签名构建，是 macOS arm64 静态构建中最可信的来源；[evermeet.cx](https://evermeet.cx/ffmpeg/) 只提供 Intel 版本。

**实测补充：**
- 在 macOS arm64 上用 imageio-ffmpeg 自带的 7.1 渲染 `xfade`、`subtitles:force_style` 和 `volume`，成功（exit 0）。
- libass 报了 "Error opening font … PingFangUI.ttc"，说明系统 CJK 字体不能直接依赖，需要自带字体并传入 `fontsdir=`。

**许可证影响：**
- FFmpeg 核心是 LGPL-2.1+。一旦启用 `--enable-gpl`（libx264 和 libx265 都要求启用它），"the GPL applies to all of FFmpeg"（[ffmpeg.org/legal](https://ffmpeg.org/legal.html)）。
- 以子进程方式调用 GPL 的 ffmpeg，属于程序之间的聚合，ArcReel 自身代码不受影响。
- 即便是进程内链接（PyAV），AGPLv3 第 13 条也允许与 GPLv3 作品组合，所以**上面所有 GPL 方案与 ArcReel 的许可证都不冲突**。
- 实际的义务是**再分发时**附上 GPL 文本和声明，并提供对应源码或书面要约。Docker 镜像再分发了 Debian 的 ffmpeg，可以用 Debian 源码包满足。pip 安装时 wheel 由 PyPI 或上游分发，ArcReel 只是声明依赖。
- 如果将来需要完全不含 GPL 的路径，只能用 BtbN 或 conda-forge 的 LGPL 构建，配合 `h264_videotoolbox` 或 libopenh264 编码 H.264。目前没有任何 pip 方案提供这种组合。

## 4. 对照表

| 候选 | 裁切 | 转场 | 多轨混音 / 音量 | 字幕烧录与样式 | 速度 / 资源 | 依赖体积 | 许可证 | Docker 镜像 | 本地安装 |
|---|---|---|---|---|---|---|---|---|---|
| **ffmpeg filtergraph（CLI）** | ✅ `trim`/`-ss` | ✅ `xfade` 几十种 | ✅ `volume`/`amix`/`sidechaincompress` | ✅ libass、ASS 样式（需构建带 libass） | 约实时 4 倍（实测）；内存随输入线性增长，需要分段 | 镜像 apt 430 MB；pip 方式 21–31 MB | GPL 构建，以子进程调用 | ✅ 现成 | ✅ 需要 imageio-ffmpeg 兜底并探测能力 |
| PyAV | ✅ | ✅（filter graph） | ✅ | ❌ 没有 libass/freetype，要走 Pillow 加 overlay | 与 ffmpeg 同一内核，但编排要自己写 | 18–39 MB | BSD 加上「推断」的 GPL | ✅ | ✅ 平台最全 |
| MoviePy | ✅ | ⚠️ 只有淡入淡出和滑动 | ✅ 没有 ducking | ⚠️ Pillow 文字，不支持 ASS | 比 ffmpeg 慢约 7 倍（实测） | 纯 Python 加 imageio-ffmpeg | MIT | ❌ 与 Pillow≥12 冲突 | ❌ 同左 |
| MLT / melt | ✅ | ✅ | ✅ | ✅ | 原生，支持多线程 | 系统包 | LGPL 加部分 GPL 模块 | ⚠️ 可以 apt 安装 | ❌ 没有 pip 包，Windows/macOS 没有 SDK |
| GES | ✅ | ✅ SMPTE 擦除 | ⚠️ 音频只有 crossfade | ⚠️ Pango textoverlay | 原生 | 约 200 MB（mac wheel） | LGPL，x264enc 为 GPL | ⚠️ 可以 apt 安装 | ❌ 没有 Linux wheel |
| Remotion | ✅ | ✅ 最丰富 | ✅ 可按帧设置音量 | ✅ HTML/CSS | 逐帧截图，最慢一档，吃 CPU | Node、Chromium 约 100+ MB、compositor | 公司许可证（≥4 人） | ⚠️ 需要加 Node 和 Chromium | ⚠️ 同左 |
| WebCodecs + Mediabunny | 需要自建 | 需要自建 | 需要自建 | 需要自建（Canvas） | 在用户机器上跑，受浏览器差异限制 | 前端包 | MPL-2.0 | 与部署形态无关 | 与部署形态无关，但 Firefox 和 Linux 上没有 AAC 编码 |
| ffmpeg.wasm | ✅ | ✅ | ✅ | ✅ libass | 比原生慢 12–25 倍，文件上限 2 GB | wasm 32 MB | GPL | 与部署形态无关 | 与部署形态无关，项目停滞 |

## 5. 推荐方案

1. **引擎：ffmpeg filtergraph 子进程。**
   - 由集级时间线编译出渲染计划（`filter_complex`），一次编码完成裁切、`xfade`/`acrossfade`、各音轨 `volume` 加 `amix`（可选 `sidechaincompress` 做闪避）以及 `ass` 字幕烧录。
   - 每路输入先规整为 `fps`、`scale`、`format=yuv420p`，以满足 xfade 的恒定帧率要求，这样也不再需要先对每段单独 normalize 一次。
   - 分段渲染以控制内存：按硬切边界切分，每段片段数设上限（实测每路 1080×1920 输入约占 330 MB），段间用 concat demuxer 加 `-c copy` 拼接。
2. **二进制来源：**
   - 查找顺序：显式配置路径 → PATH → `imageio_ffmpeg.get_ffmpeg_exe()`。
   - 把 `imageio-ffmpeg` 列为运行依赖。Docker 镜像里 PATH 上的 Debian ffmpeg 会优先命中，imageio-ffmpeg 只多占约 30 MB。
   - **对每个候选二进制，运行一次 `-filters`/`-encoders` 生成能力集并缓存，选择第一个满足需求的候选**。这样能避开 Homebrew 默认 `ffmpeg` 缺少 libass 这类「找到了 ffmpeg 但功能不全」的情况。
   - 风险：imageio-ffmpeg 自 2025-01 以来没有发版，内嵌的 FFmpeg 停在 7.x，安全更新依赖上游。需要设一个复评点。备选是在安装阶段用 `ffdl` 或 Martin Riedl 的构建下载，但这需要联网。
3. **探测：** ffprobe 缺失时，用 PyAV（进程内）读取时长和流信息。它也是 Windows arm64 和 musl 平台上唯一可用的探测手段。是否引入 PyAV（18–39 MB）可以在下游决策票里权衡；另一种做法是解析 `ffmpeg -i` 的 stderr，但不够稳健。
4. **字体：** 自带一款 OFL 许可的 CJK 字体（例如 Noto Sans CJK SC 或思源黑体的子集），渲染时通过 `ass`/`subtitles` 的 `fontsdir=` 指定，不依赖系统字体。Docker 镜像也不必为此安装 91 MB 的 `fonts-noto-cjk`。
5. **许可证：** 在 NOTICE 或文档中说明所用 ffmpeg 构建是 GPL 的，并注明源码来源（Debian 源码包，或 imageio-binaries 所引用的各上游）。

## 6. 降级路径

启动时，或者首次渲染前，先生成能力集，然后逐级降级。每次降级都要在渲染结果里标注，并告知用户或 Agent。

| 缺失的能力 | 降级做法 |
|---|---|
| `subtitles`/`ass` 滤镜 | 用 Pillow（已是本仓库依赖）预渲染每条字幕 cue 的 PNG，再通过 `overlay=enable='between(t,a,b)'` 叠加。`overlay` 属于 FFmpeg 基础滤镜，PyAV 和各构建都有。如果连这一步也不可行，就输出软字幕（MP4 `mov_text`）加外挂 `.srt`/`.ass`。 |
| `xfade` / `acrossfade` | 转场退化为硬切，与 `compose-video` 现有的短片段降级规则保持一致。 |
| `libx264` | 依次尝试 `h264_videotoolbox`（macOS）、`h264_mf`（Windows）、`libopenh264`、`mpeg4`。 |
| 内存不足或片段过多 | 缩小每段的片段数上限，退化为逐片段渲染后再拼接。 |
| 完全没有可用的 ffmpeg | 不提供成片渲染入口，或把入口置灰。退回现有的剪映草稿导出、单元素材包下载，以及前端 `PresentationPlayer` 的时间线预览；同时提示安装方式（安装 imageio-ffmpeg，或安装带 libass 的系统 ffmpeg，例如 Homebrew 的 `ffmpeg-full`）。浏览器端 WebCodecs 渲染作为远期选项。 |

## 附：实测脚本要点

- 测试素材由 `testsrc2=size=1080x1920:rate=24:duration=10` 和 `sine` 生成，共 6 段，BGM 为 60 秒的 sine 音频。
- ffmpeg 的滤镜图结构：`[k:v]trim=1:9,setpts=PTS-STARTPTS,fps=24,format=yuv420p` → 串联 `xfade=transition=fade:duration=0.5:offset=…` → `ass=s.ass`；音频是 `atrim` → 串联 `acrossfade=d=0.5` → 与 `volume=0.3` 后的 BGM 做 `amix=inputs=2:duration=first:normalize=0`。
- 峰值内存通过 Python 的 `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` 统计。测试素材是合成画面，比真实素材更容易编码，实际耗时会偏高。
