---
name: compose-video
description: 把已生成的视频片段按剧本顺序拼接为单集成片，可选混入 BGM、场景间转场与台词字幕烧录。当用户说"拼成片"、"合成本集视频"、"加背景音乐"或"加字幕"时使用。
---

# 合成视频

把单集已生成的视频片段（`videos/*.mp4`）按剧本顺序串接为一段成片，写入 `output/`。可选混入 BGM、按 `transition_to_next` 添加场景间转场。

## 适用范围（重要）

- **支持 drama 与 reference_video 模式** — 脚本读取剧本顶层 `scenes[]`（drama）或 `video_units[]`（reference_video 路线）；narration（`segments[]`）、ad（`shots[]`）会被脚本拒绝。这些模式的成片导出请走 Web 端剪映草稿导出（ad 草稿含视频轨 + 口播文案字幕轨，导出后在剪映配音成片）
- **字幕来源随模式** — `--subtitles` 时：drama 从场景级 `utterances`（台词 + 画外音）派生；reference_video 从 unit 的 `shots[*].text` 台词行（`@[角色]：{台词}` 对话行 / `{台词}` 画外音行，与剪映导出同口径）派生。时间轴默认先用本地 faster-whisper 语音识别把每条字幕配到真实语音边界，模型不可用时自动回退 silencedetect（开场静音偏移 + 溢出缩放，见下文）。烧录用显式样式的 ASS（字号 / 底部边距 / 描边按画布分辨率计算，长台词按宽度硬换行），不再依赖 libass 对 SRT 的默认样式
- **单集拼接** — 一次只处理一份剧本文件，不支持多集合并
- **不实现片头片尾 / BGM 音量调节** — 这些需求请走 Web 端剪映草稿导出

## 声音与字幕的真相源

每个单元的**声音归属**（provider 原音、可选旁白 TTS）与**字幕时序**由服务端 presentation 结果统一
决定；预览、下载与剪映草稿导出消费的是同一份。本 skill 只做片段串接与可选 BGM 混入：

- **不静音、不闪避、不分离 provider 原音**，也不改写源片段文件。混入 BGM 时由 ffmpeg `amix`
  等比缩放两路输入，这是既有的混音行为，不是本 skill 在做音量决策；不混 BGM 时原音原样透传
- **不自行估算字幕时间轴**，也不生成字幕。需要字幕轨请走 Web 端导出
- **不替用户判断 TTS 是否必需**。旁白交付选「后期配音」时视频照常成片，缺 TTS 不是缺口
- 时长以媒体实际时长为准，不用剧本计划的 `duration_seconds` 反推声画边界

stale 产物照常参与成片，不因「看起来旧」跳过或触发重生。

## CLI 用法

脚本必须在含 `project.json` 的项目 cwd 内运行，并使用**相对项目根 cwd** 的剧本文件名：

```bash
# 最简形式：按剧本顺序拼接 + 自动转场（按 transition_to_next）
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json

# 混入 BGM（音乐文件相对项目根 cwd 或绝对路径）
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --music background_music.mp3

# 关闭转场（一律 cut 拼接，可用于规避 xfade 编码不一致问题）
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --no-transitions

# 自定义输出文件名（输出固定落在 output/ 下）
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --output episode_1_final.mp4

# 烧录台词字幕（剧本 utterances 派生，默认 ASR 语音识别对齐 + silencedetect 兜底，需 ffmpeg 带 libass）
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --subtitles

# 不想用语音识别（更快但可能不同步）：跳过 ASR，仅 silencedetect 对齐
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --subtitles --no-asr

# 完全关闭音频对齐（纯按语速估算）
python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json --subtitles --no-audio-align
```

完整参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `script` | 位置参数（必填） | 剧本文件名（相对项目 cwd） |
| `--output OUTPUT` | 可选 | 输出文件名；缺省按剧本 `novel.chapter` 字段生成。无论何种取值，最终都落在 `output/` 子目录内 |
| `--music MUSIC` | 可选 | BGM 文件路径（相对项目 cwd 或绝对路径），但**必须解析后位于项目目录内** |
| `--no-transitions` | flag | 全部用 cut 直接拼接，忽略剧本里的 `transition_to_next` |
| `--subtitles` | flag | drama 按每场景 `utterances`、reference_video 按 unit `shots[*].text` 台词行，把台词 + 画外音烧录为画面底部字幕；时长按 `lib.speech_rate` 语速估算，与剪映导出同口径。需 ffmpeg 编译 libass（预检失败会给出安装指引） |
| `--no-asr` | flag | 跳过 faster-whisper 语音识别对齐（回退 silencedetect；默认开启，见下方说明） |
| `--no-audio-align` | flag | 关闭字幕音频对齐（仅按语速估算） |

## 工作流程

1. **读剧本** — 通过 `ProjectManager.load_script()` 从 `scripts/` 加载（路径过滤复用 lib 内 `_safe_subpath`）
2. **收集片段** — 按 `scenes[i].generated_assets.video_clip`（drama）或 `video_units[i].generated_assets.video_clip`（reference_video）逐个解析视频文件并校验存在
3. **拼接** — 默认走 normalize → concat（先把每段规范化为统一 H.264/AAC，再用 concat filter 编码），有 `xfade` 转场需求时按 `transition_to_next` 加滤镜
4. **字幕** — 若指定 `--subtitles`，drama 按场景 `utterances`、reference_video 按 unit `shots[*].text` 台词行派生时间片，默认用 **faster-whisper**（本地 CPU，word-level 时间戳）把每条台词按文本相似度贪心配到真实语音边界，前后各留 0.1~0.15s 余量；配对失败或模型不可用（未安装 / 下载失败 / 转写无结果）自动回退 **silencedetect** 对齐：开场静音超过 0.3s 时整体后移（避免「人未开口字幕先出」）、估算总时长超过场景可用时长时按比例压缩、相邻字幕条间留 0.25s 留白。随后把时间片渲染为显式样式 ASS（`PlayResX/Y` 按目标分辨率、字号约画布高 6% 且超长台词自动缩档保证最多 5 行、底部边距约画布高 5.5%、描边阴影、长台词按宽度 `\N` 硬换行且任何一行都不超出画面宽度）并在 normalize 阶段烧录进画面（各片段字幕时间轴相对自身，转场 / concat 后仍与场景对齐）
5. **混音** — 若指定 `--music`，再做一遍 audio mix（`-c:v copy`，保留已烧字幕）；输出文件名追加 `_with_music`

## 支持的转场类型

按剧本字段 `scenes[i].transition_to_next` / `video_units[i].transition_to_next` 映射：

| 字段值 | ffmpeg 行为 |
|---|---|
| `cut`（默认） | 直接拼接，无淡入淡出 |
| `fade` | `xfade=transition=fade:duration=0.5` |
| `dissolve` | `xfade=transition=dissolve:duration=0.5` |
| `wipe` | `xfade=transition=wipeleft:duration=0.5` |

## 前置检查

- [ ] 当前 cwd 是项目根（含 `project.json`）
- [ ] 剧本顶层有 `scenes[]`（drama）或 `video_units[]`（reference_video 路线）
- [ ] 每个场景的 `generated_assets.video_clip` 都已生成
- [ ] `ffmpeg` / `ffprobe` 可用（脚本预检：先查 PATH，再查 Windows 常见安装位置；缺失时给出安装指引）
- [ ] BGM 文件存在（如指定 `--music`）
- [ ] ffmpeg 带 libass（如指定 `--subtitles`；脚本用 `ffmpeg -filters` 预检 `subtitles` 滤镜，缺失时给出换用完整版的指引）
- [ ] faster-whisper（可选，推荐）：`uv add faster-whisper` 后首次运行会下载 base 模型（约 140MB）；网络受限时可设 `HF_ENDPOINT=https://hf-mirror.com`。未安装 / 下载失败不影响合成，自动回退 silencedetect

## Windows 环境说明

- 脚本定位 ffmpeg/ffprobe 的顺序：PATH → Git for Windows / MSYS2 的 `mingw64\bin`、`usr\bin` → `C:\ffmpeg\bin`、`C:\Program Files\ffmpeg\bin` → 用户级 `%LOCALAPPDATA%\ffmpeg\bin` → winget 安装目录（`%LOCALAPPDATA%\Microsoft\WinGet\Packages`）
- 都找不到时脚本退出并给出安装指引；Windows 推荐 `winget install --id Gyan.FFmpeg -e`
- agent 的 Bash 工具是非登录 shell，不读 `~/.bashrc`；若在 `~/.bashrc` 里手动加过 PATH，重启 git bash 才会生效
- faster-whisper 在 CPU 上用 int8 推理；每场景 6~12s 音频转写约数秒到十几秒，成片时长不变

## 限制 / 缺失能力

下列能力**未实现**，请使用 Web 端剪映草稿导出：

- narration / ad 模式（脚本只识别 `scenes[]` / `video_units[]`）
- 多集合并 / 单集分片裁剪
- BGM 音量调节、独立 BGM 时间轴
- 片头片尾 intro/outro
