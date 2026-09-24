# 剪辑时间线的数据格式：OTIO / FCPXML / MLT XML / 剪映草稿（pyJianYingDraft）覆盖面

> 状态：调研完成，结论供地图 [#2667](https://github.com/ArcReel/ArcReel/issues/2667)（Wayfinder: Agent 自动剪辑）汇总。
> 关联：[#2671](https://github.com/ArcReel/ArcReel/issues/2671)（本票）、[#1931](https://github.com/ArcReel/ArcReel/issues/1931)（pyjianyingdraft 0.3 迁移）。
> 数据日期：2026-09-24。版本与发布时间来自 PyPI JSON API；仓库许可证与活跃度来自 GitHub REST API（`gh api`）；pyJianYingDraft 的能力结论来自 PyPI 上 0.2.7 与 0.3.0 两个 wheel 的源码逐文件对比；OTIO 的能力结论来自其仓库 `docs/tutorials/*.md`、C++ 头文件与 0.18.1 wheel；FCPXML 来自 Apple Developer「FCPXML Reference」；MLT XML 来自 mltframework.org 官方文档。

## 结论一句话

**选方案一：自定义领域模型 + 各格式适配器（剪映草稿、成片渲染、可选的 OTIO 导出）。**剪辑时间线需要的关键语义（ArcReel 单元与视频版本的引用、字幕与样式、音量与关键帧、BGM 闪避）OTIO 都没有对应的 schema，只能塞进无类型的 `metadata` 字典。这等于在 OTIO 外壳里另造一套私有模型，同时还要背上一个 Beta 版、无类型存根的 C++ 扩展依赖。两个主要输出端（剪映草稿、ffmpeg 渲染）都没有现成的 OTIO 适配器，这部分工作一点也省不下来。现有的 `SpeechPresentation` 已经是「一个模型，多个适配器」的结构，剪辑时间线应该沿这条路扩展到集级别。

## 结论速览

| 问题 | 一行结论 |
|---|---|
| OTIO 成熟度 | ASWF 项目，Apache-2.0，最新版 0.18.1（2025-11-09），GitHub release 仍标为 pre-release（Beta）。约一年一个版本，仓库活跃（2026-09-22 仍有推送）。Python 包是 pybind11 编译的扩展，wheel 里没有 `.pyi`/`py.typed`，只有 cp39–cp313 的 wheel。 |
| OTIO 表达力 | 裁切（`Clip.source_range`）、多音轨（`Track.kind=Audio`）、转场（`Transition` 的 in/out offset）、任意元数据、多媒体引用（可用来挂视频版本）都能原生表达。**字幕、音量、关键帧、文本样式与位置**没有原生 schema；转场类型只有 `SMPTE_Dissolve` 和 `Custom_Transition` 两种。 |
| OTIO 适配器 | 核心包只内置 `otio_json`/`otiod`/`otioz`。FCP7 XML、AAF、CMX3600 等需要装 `OpenTimelineIO-Plugins`。FCPXML（FCPX）适配器不在插件包里，停在 1.0.0（2023-07）。**没有剪映适配器，也没有 ffmpeg 渲染适配器。** |
| FCPXML / MLT XML | 两者都能完整表达裁切、多轨、转场、音量关键帧、字幕或标题以及自定义元数据，可以作为「给专业剪辑软件的导出目标」，但都不适合作为内部模型。建议暂不做，将来按需求再加适配器。 |
| 剪映 0.2.x 与 0.3.x | 两者的**编辑表达力基本相同**。0.3.0 的差异在于：用 `append_track(s)`/`insert_track(s)` + `TrackRef`/`TrackSpec` 取代 `add_track`，重写轨道排序以适配新版剪映（10.8），新增色度抠图和「音色 / 声音成曲」音频效果，加入 `fallback_loader` 草稿加载接口，并删除了 snake_case 兼容类名。已知问题：0.3.0 生成的蒙版在剪映 10.8 上不生效。 |
| 推荐 | 采用自定义领域模型（整数微秒时间、冻结的 dataclass、带不变量校验），通过适配器输出剪映草稿、成片渲染和可选的 `.otio`。剪映草稿的能力上限见 §6，应作为剪辑时间线表达力的**上限**，而不是由模型自由扩张。 |

## 1. ArcReel 现状（仓库核实）

- `server/services/presentation/jianying_draft_service.py`：依赖 `pyjianyingdraft>=0.2.6,<0.3`（`pyproject.toml:31`），`uv.lock` 锁定在 0.2.7。服务的整个输出语法是：一条视频轨，可选一条「字幕」文本轨，可选一条「旁白」音频轨。每个单元对应一个 `VideoSegment`，已经传入 `source_timerange=trange(video.start_microseconds, video.duration_microseconds)`，所以**适配器层已经支持裁切**，只是上游永远给出从 0 开始的整段。`volume=video_track.gain`。转场只映射 `fade → 闪黑`、`dissolve → 叠化`，使用预设的默认时长。字幕样式和位置写死，只按横竖屏区分。服务通过 `add_track` 调用了 3 次（也就是 #1931 的阻塞点）。
- `lib/speech/speech_presentation.py`：`SpeechPresentation` 是逐单元的呈现模型，由浏览器播放器、素材包下载和剪映适配器共用。字段包括 `unit_id`、`variant`、`selection`/`currency`、`VideoPresentationTrack(media, start_microseconds, duration_microseconds, audio_enabled, gain)`、`NarrationPresentationTrack(...)`、`subtitles: tuple[SubtitleCue]`（`start/duration/text/owner/speaker`），以及 `subtitle_basis`/`presentation_basis`。`PresentationMedia` 带有 `artifact_path`、`version`、`selection`、`currency` 和生成证据。模块的 docstring 明确写着「editor serialization remain adapters」。
- `transition_to_next` 的取值是 `cut|fade|dissolve`（`server/routers/reference_videos.py:114`、`lib/project/project_migrations/v6_to_v7_ad_reference_video_units.py:29`）。
- 时间单位全程使用**整数微秒**，与剪映草稿的单位一致（pyJianYingDraft 的 `Timerange` 也使用微秒）。

**推论：**现有代码已经是「领域模型 → 编辑器语法适配器」的分层，缺的只是**集级别**的时间线：跨单元的轨道、BGM、裁切与闪避。

## 2. OpenTimelineIO

### 2.1 成熟度、维护与许可证

| 项 | 事实 | 来源 |
|---|---|---|
| 许可证 | Apache-2.0 | `gh api repos/AcademySoftwareFoundation/OpenTimelineIO`；PyPI `license: Apache 2.0 License` |
| 版本节奏 | 0.15（2022-09）→ 0.16（2024-04）→ 0.17（2024-06）→ 0.18.0（2025-11-06）→ 0.18.1（2025-11-09） | PyPI releases |
| 稳定性标记 | GitHub release 全部是 **Pre-release**，标题为「Beta 18」 | `gh release list` |
| 活跃度 | 约 2.0k stars，212 个 open issue，最后推送 2026-09-22 | GitHub API |
| Python 形态 | `_otio.cpython-312-darwin.so` 与 `_opentime...so` 为 pybind11 扩展，**wheel 内没有 `.pyi` 和 `py.typed`** | 0.18.1 cp312 macOS arm64 wheel |
| wheel 覆盖 | cp39–cp313 × macOS arm64 / manylinux x86_64、aarch64 / win_amd64，**没有 cp314**；`requires_python >3.9.0` | PyPI 0.18.1 files |
| 0.18 主要变化 | `Effect.enabled`、Color 原语、按 Track 或 Clip 着色、自定义 adapter hook、移除 OTIOView | v0.18.0 release notes |

对 ArcReel 的影响：项目要求 `requires-python >=3.12`，并以 `basedpyright --warnings` 作为闸门。OTIO 没有类型存根，所有对象都会被当作 Unknown，需要自己写 stub 或加豁免。另外，Python 新版本出来后，OTIO 的 wheel 往往要滞后一段时间才跟上。

### 2.2 适配器生态

来源：OTIO 仓库 `docs/tutorials/adapters.md`；PyPI `opentimelineio-plugins` 0.18.1 的 `requires_dist`。

- **核心内置：**`otio_json`（`.otio`）、`otiod`（目录包）、`otioz`（zip 包，含媒体）。
- **`OpenTimelineIO-Plugins` 捆绑：**AAF、ALE、burnins、CMX3600（EDL）、`fcp_xml`（FCP7 XML）、maya_sequencer、svg、xges。文档原话是：这些适配器「may be maintained and supported at varying levels」。
- **其他：**kdenlive（由 KDE 维护）、`fcpx_xml`（`otio-fcpx-xml-adapter` 1.0.0，2023-07-07 发布，仓库最后推送在 2024-06，7 stars）、hls_playlist。
- **缺口：**没有剪映或 CapCut 适配器，也没有 ffmpeg 渲染适配器。

### 2.3 表达力逐项核对

来源：`docs/tutorials/otio-serialized-schema.md`、`src/opentimelineio/transition.h`、`track.h`。

| 需求 | OTIO 能否原生表达 | 说明 |
|---|---|---|
| 素材裁切（source range） | ✅ | `Clip.source_range: TimeRange`，时间用 `RationalTime(value, rate)` 表示 |
| 多轨、多音轨 | ✅ | `Stack` 下挂多条 `Track`，`Track.kind` 只有 `Video` / `Audio` 两个取值 |
| 空隙 | ✅ | `Gap` |
| 转场 | 🟡 | `Transition(in_offset, out_offset, transition_type)` 可以表达重叠区间。`transition_type` 只有 `SMPTE_Dissolve` 和 `Custom_Transition` 两种，其他转场（闪黑、擦除等）靠 `name`/`metadata` 约定 |
| 音量 / 增益 | ❌ | 没有 schema，只能用通用 `Effect(effect_name, metadata)` 自行约定 |
| 关键帧（音量包络、位移缩放） | ❌ | 没有关键帧 schema |
| 变速 | ✅ | `LinearTimeWarp`、`FreezeFrame` |
| 字幕 / 文本 | ❌ | 没有 caption 或 text schema，也没有 `Track.kind=Subtitle`。常见做法是用 `Marker` 或带 metadata 的 `Clip` 自行约定，样式和位置同样没有 schema |
| 空间变换（位置 / 缩放） | ❌ | 只有媒体引用上的 `available_image_bounds`，没有 clip 级别的 transform |
| 自定义元数据 | ✅ | 所有 `SerializableObjectWithMetadata` 都带 `metadata` 字典，可以放 `unit_id`、`artifact_path`、`version`、basis digest |
| 视频版本 | ✅ | `Clip.media_references`（字典）+ `active_media_reference_key`，可以在一个 clip 上挂多个版本并指定当前生效的那个 |
| 自定义 schema | ✅ | 可通过 `SchemaDef` 插件注册，但注册后的 schema 也只有自家工具能理解 |

**小结：**OTIO 擅长的是「剪辑决策」（哪段素材的哪一段放在哪条轨的哪个位置，以及转场重叠）。它刻意不描述「效果与呈现」，比如音量、字幕、样式、关键帧，而这些恰好是 ArcReel 剪辑时间线的核心内容，也是剪映草稿能承接的内容。

## 3. FCPXML（仅作为导出目标对照）

来源：Apple Developer「FCPXML Reference」及其子页（Story Elements、transition、caption、adjustment-elements、animation、metadata、Document Type Definition）。

- **结构：**`resources`（asset / format / effect）+ `spine` 主故事线，其他元素以 lane 锚定在主故事线上。lane 越大，合成层级越高；音频的 lane 不影响合成。
- **裁切：**`asset-clip` 的 timing attributes（`offset`/`start`/`duration`）。
- **转场：**`transition` 元素位于 spine 内，用 `offset`/`duration` 定位，效果由 `filter-video`/`filter-audio` 指定。音频交叉淡化和 J/L-cut（`audioStart`/`audioDuration`）也能表达。
- **音量与关键帧：**`adjust-volume` 等 adjust-* 元素可配合 `param` + `keyframeAnimation` 做关键帧动画。
- **字幕：**从 FCPXML 1.8 开始有 `caption` 元素（闭路字幕或 subtitle，可带样式化的 `text` 块）；另外有 `title`。
- **元数据：**`metadata/md key=value`。
- **版本：**官方页面给出的 DTD 版本为 1.10，更早的版本（1.5–1.9）在支持页上。
- **价值：**适合给 Final Cut Pro 用户使用，表达力足以承接 ArcReel 的整条时间线。代价是需要按 DTD 手写序列化（时间以有理数秒表示，例如 `1001/30000s`），而 OTIO 的 fcpx 适配器维护状态较弱（见 §2.2），不宜依赖。

## 4. MLT XML（仅作为导出目标对照）

来源：mltframework.org「MLT XML」文档；`gh api repos/mltframework/mlt`（LGPL-2.1，v7.40.0 于 2026-06-25 发布，活跃）。

- **结构：**`producer`（素材）→ `playlist`（一条轨，`entry in/out` 以帧为单位裁切）→ `tractor`/`multitrack`（多轨）+ `transition`/`filter`。任意 `property` 都能挂自定义属性。
- **价值：**这是 Shotcut / Kdenlive 的工程格式，`melt` 命令行可以直接渲染，理论上可以作为「渲染后端」。但 ArcReel 已经在用 ffmpeg，引入 MLT 等于多一个原生依赖，而且帧单位与微秒模型之间还要做换算。**不建议作为内部模型或渲染后端。**是否作为导出目标，看是否有 Kdenlive / Shotcut 用户的需求。

## 5. 剪映草稿与 pyJianYingDraft

来源：PyPI `pyjianyingdraft` 0.2.7 与 0.3.0 wheel 源码对比；上游 README（main 分支）；GitHub release notes（0.2.7、0.3.0）；`gh api repos/GuanYixuan/pyJianYingDraft`（Apache-2.0，约 4.4k stars，50 个 open issue，最后推送 2026-07-08）。

### 5.1 版本与状态

- 0.2.7（2026-06-25）是 0.2 系列的最后一个版本。其 release notes 原话：「本次更新会带来公共接口变更，因而推荐不依赖新版本功能的用户停留在 `0.2` 系列」。
- 0.3.0 于 2026-07-08 发布，README 标注「经历了大规模更新，若有相关功能问题欢迎提出 issue」。
- 平台：Windows、Linux、macOS 都能生成草稿，但**自动导出（渲染）只支持 Windows，并且剪映版本需在 6 及以下**。剪映 7 及以上版本不支持自动导出。
- 新版剪映的 `draft_content.json` 往往不是明文 JSON。因此「读取已有草稿」（模板模式）需要用户自行接入 `fallback_loader`，上游明确表示不内置解密实现。**写出新草稿不受影响**，README 的功能表对新版剪映 10.8 大多标注 ✅。

### 5.2 能力矩阵（0.2.7 与 0.3.0 源码核对）

| 能力 | API | 0.2.7 | 0.3.0 | 备注 |
|---|---|---|---|---|
| 素材裁切 | `VideoSegment/AudioSegment(material, target_timerange, source_timerange=...)` | ✅ | ✅ | 超出素材时长会抛 `ValueError` |
| 恒定变速 | `speed=`、`change_pitch=` | ✅ | ✅ | 同时给出 source 和 speed 时，以两者重算 target 时长 |
| 片段音量 | `volume=`（视频与音频片段都支持） | ✅ | ✅ | |
| 音量关键帧 | `AudioSegment.add_keyframe(t, volume)`；视频片段 `add_keyframe(KeyframeProperty.volume, …)` | ✅ | ✅ | **只支持线性插值**（`curveType: "Line"`） |
| 音频淡入淡出 | `AudioSegment.add_fade(in, out)`；`VideoSegment.add_fade` | ✅ | ✅ | 每个片段只能有一组 |
| 多音轨 | 0.2：`add_track(TrackType.audio, name)`；0.3：`append_track(TrackSpec(TrackType.audio, name, mute))` | ✅ | ✅ | 同类轨道超过一条时，`add_segment` 必须指定轨道 |
| 轨道层级 | 0.2：`relative_index`/`absolute_index`；0.3：`insert_track(under_track=/over_track=/at_index=)` | ✅ | ✅ | 0.3 按全局 `track_order` 重新计算 `render_index` |
| 视频画面变换 | `ClipSettings(alpha, flip, rotation, scale_x/y, transform_x/y)` | ✅ | ✅ | 坐标是归一化值 |
| 画面关键帧 | `KeyframeProperty`：position_x/y、rotation、scale_x/y、uniform_scale、alpha、saturation、contrast、brightness、volume | ✅ | ✅ | 不支持特效或滤镜参数的关键帧（README） |
| 转场 | `VideoSegment.add_transition(TransitionType, duration=)` | ✅ | ✅ | 转场挂在**前一个**视频片段上；预设共 453 个，其中非 VIP 130 个 |
| 视频特效 / 滤镜 / 动画 | `add_effect`/`add_filter`/`add_animation`，以及独立特效轨 `add_effect(…, t_range)` | ✅ | ✅ | 场景特效 1097 个、滤镜 1052 个、入场动画 155 个等，都是在线资源 ID |
| 蒙版 / 混合模式 / 背景填充 | `add_mask`/`set_mix_mode`/`add_background_filling` | ✅ | ✅ | 0.3.0 的蒙版在剪映 10.8 上不生效（README 注明「预计在 0.3.1 中修复」） |
| 色度抠图 | `VideoSegment.add_chroma` | ❌ | ✅ | 0.3 新增 |
| 音频效果 | `AudioSegment.add_effect`（场景音；0.3 增加 `ToneEffectType` 音色和 `SpeechToSongType` 声音成曲） | 部分 | ✅ | 声音成曲在 5.9 上不生效 |
| 文本与样式 | `TextSegment(text, timerange, font, style=TextStyle(...), clip_settings, border, background, shadow)` | ✅ | ✅ | `TextStyle` 字段：size、bold、italic、underline、color(RGB)、alpha、align(0/1/2)、vertical、letter_spacing、line_spacing、auto_wrapping、max_line_width |
| 文本位置 | `clip_settings=ClipSettings(transform_x/y, scale…)` | ✅ | ✅ | 文本关键帧只支持位置和大小相关属性 |
| 花字 / 气泡 / 文本动画 | `add_bubble`/`add_effect`/`add_animation` | ✅ | ✅ | 需要剪映资源 ID |
| 字体 | `FontType` 枚举（798 个） | ✅ | ✅ | 未缓存的字体需要二次打开草稿 |
| 导入 SRT | `import_srt(path, track, time_offset, style_reference, text_style, clip_settings)` | ✅ | ✅ | |
| 自定义元数据 | 只有 `VideoMaterial/AudioMaterial(path, material_name)` 与轨道名 | — | — | **片段上没有任意元数据字段** |

**结论：**0.2.x 与 0.3.x 在编辑表达力上几乎一致，差别集中在轨道 API、新版剪映兼容性和少数新增效果上。升级到 0.3 的主要收益是适配新版剪映的轨道排序，而不是获得新的剪辑能力。

### 5.3 0.3 的 API 变化（对应 #1931）

来源：0.3.0 release notes；`_script_file_tracks.py`、`track.py`、`__init__.py` 的差异。

1. `ScriptFile.add_track(track_type, track_name, *, mute, relative_index, absolute_index)` 被**删除**，替代为：
   - `append_track(TrackSpec(track_type, name=None, mute=False)) -> TrackRef`，放到最上层；
   - `append_tracks([...]) -> tuple[TrackRef, ...]`；
   - `insert_track(spec, *, under_track=|over_track=|at_index=)`，三个定位参数只能给一个；
   - `insert_tracks(...)`。
2. `add_segment(segment, track: str | TrackRef | None)`：第二个参数从 `track_name: str` 改为同时接受名字和 `TrackRef`。
3. `ScriptFile.load_template` 改为私有的 `_load_template`，读取入口统一走 `DraftFolder(folder, fallback_loader=...)`；新增异常 `DraftContentLoadFailed`。
4. 删除 0.2.0 引入的 snake_case 兼容类名（`Script_file`、`Track_type` 等）。
5. 顶层新导出 `TrackRef`、`TrackSpec`、`MixModeType`、`ToneEffectType`、`SpeechToSongType`。
6. `Timerange.import_json` 容忍缺少 `start` 字段。

ArcReel 的迁移只涉及 `jianying_draft_service.py` 的 3 处 `add_track`：改成一次 `append_tracks([TrackSpec(video), TrackSpec(text, "字幕"), TrackSpec(audio, "旁白")])`，`add_segment` 的第二个参数可以继续传名字。#1931 的阻碍点（草稿正确性靠类型检查和单测盖不住）与本结论无关，仍然成立。

## 6. 剪映草稿的能力上限，以及它对剪辑时间线的约束

剪映草稿是主要输出端，所以下面这些限制应当成为剪辑时间线模型的**设计边界**：模型能表达的东西，要么能落到剪映，要么在导出时有明确的降级规则。

1. **同一轨道上的片段不能重叠**（`Track.add_segment` 会抛 `SegmentOverlap`）。音频交叉淡化、J/L-cut、两段 BGM 衔接都必须拆到不同轨道，模型里应允许同一角色（例如 BGM）有多条 lane。
2. **转场只存在于视频轨，并挂在前一个片段上**，只能从预设枚举中选，时长可以设置，没有音频转场。音频只能用片段的淡入淡出和音量关键帧来实现。转场的真实语义（是否与下一个片段重叠，即 `is_overlap`）由预设决定。
3. **主视频轨（最底层）开启磁吸**（`maintrack_adsorb` 默认为 True）后，片段会被对齐到 0s，中间的空隙会被吸掉。模型里的「黑场或留白」不能靠主轨空隙表达，需要改用黑场素材、上层轨道，或关闭磁吸（后者需要在剪映中实测）。
4. **关键帧只支持线性插值**，属性限于位置、旋转、缩放、不透明度、饱和度、对比度、亮度和音量。音频片段只支持音量关键帧；特效和滤镜参数不能做关键帧。BGM 闪避（ducking）可以用音量关键帧表达，但缓动曲线不能。
5. **变速只能是整段恒定速度**，不支持曲线变速。
6. **文本样式的上限就是 `TextStyle` + 描边、背景、阴影 + 字体枚举 + 花字、气泡、动画**。位置用归一化的 `transform_x/y` 表示。逐字多样式只能通过模板替换实现（依赖草稿可读）。剪映没有独立的「字幕」语义，字幕就是文本轨上的文本片段，说话人区分只能靠不同样式或不同轨道。
7. **特效、滤镜、转场、动画、花字都是剪映的在线资源 ID**：打开草稿时由剪映下载，可能出现「加载失败」；VIP 资源需要会员才能导出。模型只应引用一小组经过验证的非 VIP 预设（目前只用了闪黑和叠化），不宜暴露完整枚举。
8. **草稿里没有任意元数据字段**，只有素材名和轨道名。导出是**单向的**：用户在剪映中改过的草稿，无法可靠地映射回 ArcReel 的单元和视频版本；而且新版草稿本身也需要 `fallback_loader` 才能读取。回写或双向同步不在可行范围内。
9. **草稿引用本地绝对路径的素材**（ArcReel 目前在打包后改写路径前缀），也没有复合片段或嵌套序列（README 说只能通过模板实现）。
10. **草稿的渲染由用户在剪映里手动完成。**自动导出只支持 Windows 且剪映版本需 ≤ 6，所以服务端的「成片」必须走自己的渲染适配器（ffmpeg），不能依赖剪映。

## 7. 推荐：自定义领域模型 + 适配器，而不是以 OTIO 作为模型

**理由：**

1. **语义缺口在 OTIO 那一侧。**剪辑时间线必须表达单元身份、视频版本选择、时效性（current / stale）、字幕 cue 与说话人、音量与闪避、文本样式与位置。其中只有「版本」能借用 OTIO 的 `media_references`，其余都要放进无类型的 `metadata`。这实际上还是一套私有模型，只是换了个外壳，失去了 ArcReel 现有 frozen dataclass 的不变量校验和 basedpyright 类型检查。
2. **没有适配器可以复用。**主要输出端是剪映草稿和 ffmpeg 渲染，OTIO 生态里都没有对应的适配器，这两个都得自己写。OTIO 能免费提供的只有 `.otio` 和经插件转出的 EDL / FCP7 XML / AAF，这些是次要需求。
3. **依赖成本。**OTIO 处于 Beta（0.x、pre-release），是 C++ 扩展，没有类型存根，wheel 目前只到 cp313。对一个只用来做可选导出的依赖来说，放在核心模型里代价过高。放在可选适配器里，它的依赖范围和风险都可以被隔离。
4. **时间单位一致。**ArcReel 和剪映都用整数微秒。OTIO 用 `RationalTime(value, rate)`，FCPXML 用有理数秒，MLT 用帧。以微秒作为模型单位，在各个适配器里再换算，是转换次数最少的做法。
5. **沿用现有架构。**`SpeechPresentation` 已经被浏览器播放器、素材包下载和剪映导出三方共用，模块注释也明确写着「editor serialization remain adapters」。集级剪辑时间线应该组合逐单元的呈现结果，再加上集级的轨道、裁切、BGM 和转场，而不是另起一套外部格式。

**建议的形状**（供后续设计票参考，不是定稿）：

- 模型：`EditTimeline(episode, canvas, tracks)`。轨道的角色包括 video / narration / original-audio / bgm / subtitle，同一角色允许多条 lane。时间与区间一律用整数微秒。片段通过 `PresentationMedia` 引用单元和版本，并带有 `source_range`。转场限定为一个有限集合，并为每种转场写明到剪映预设和 ffmpeg `xfade` 的映射。音量支持线性关键帧。字幕使用有限的样式预设。
- 表达力上限直接取 §6，模型校验层负责拦截剪映无法承接的组合，例如同一 lane 上的重叠、非线性关键帧、音频转场等。
- 适配器：
  1. 剪映草稿（随 #1931 升级到 0.3）；
  2. ffmpeg 成片渲染（取代 `compose-video` skill 中直接读取 `video_clip` 的旧路径）；
  3. 可选：`.otio` 导出（只用核心包，放在可选依赖组里），或者直接写 FCPXML。两者都放到有明确用户需求时再做。

## 附：来源清单

- PyPI JSON：`https://pypi.org/pypi/pyjianyingdraft/json`、`/0.2.7/json`、`/0.3.0/json`；`https://pypi.org/pypi/opentimelineio/json`、`/0.18.1/json`；`opentimelineio-plugins`、`otio-fcpx-xml-adapter`、`otio-fcp-adapter`
- pyJianYingDraft 源码（wheel）：`script_file.py`、`_script_file_tracks.py`、`_script_file_segments.py`、`track.py`、`segment.py`、`video_segment.py`、`audio_segment.py`、`text_segment.py`、`keyframe.py`、`draft_folder.py`、`metadata/*.py`
- pyJianYingDraft README（main）与 release notes：`https://github.com/GuanYixuan/pyJianYingDraft/releases/tag/0.3.0`、`/tag/0.2.7`
- OTIO：`docs/tutorials/adapters.md`、`docs/tutorials/otio-serialized-schema.md`、`src/opentimelineio/transition.h`、`src/opentimelineio/track.h`；release `v0.18.0` notes
- Apple：`https://developer.apple.com/documentation/professional-video-applications/fcpxml-reference` 及子页 story-elements、transition、caption、adjustment-elements、animation、metadata、document-type-definition
- MLT：`https://www.mltframework.org/docs/mltxml/`
- 仓库：`server/services/presentation/jianying_draft_service.py`、`lib/speech/speech_presentation.py`、`server/routers/reference_videos.py`、`pyproject.toml`、`uv.lock`
