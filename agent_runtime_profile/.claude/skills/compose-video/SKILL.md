---
name: compose-video
description: 把已生成的视频片段按剧本顺序拼接为单集成片，可选混入 BGM 与场景间转场。当用户说"拼成片"、"合成本集视频"或"加背景音乐"时使用。
---

# 合成视频

把单集已生成的视频片段（`videos/*.mp4`）按剧本顺序串接为一段成片，写入 `output/`。可选混入 BGM、按 `transition_to_next` 添加场景间转场。

## 适用范围（重要）

- **仅 剧情演绎** — 脚本读取剧本顶层 `scenes[]`；narration（`segments[]`）、ad（`shots[]`）和 reference_video（`video_units[]`）会被脚本拒绝。这些模式的成片导出请走 Web 端剪映草稿导出（ad 草稿含视频轨 + 口播文案字幕轨，导出后在剪映配音成片）
- **单集拼接** — 一次只处理一份剧本文件，不支持多集合并
- **不实现片头片尾 / BGM 音量调节** — 这些需求请走 Web 端剪映草稿导出

## 声音与字幕的真相源

每个单元的**声音归属**（供应商原音、可选旁白 TTS）与**字幕时序**由服务端 presentation 结果统一
决定；预览、下载与剪映草稿导出消费的是同一份。本 skill 只做片段串接与可选 BGM 混入：

- **不静音、不闪避、不分离 供应商原音**，也不改写源片段文件。混入 BGM 时由 ffmpeg `amix`
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
```

完整参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `script` | 位置参数（必填） | 剧本文件名（相对项目 cwd） |
| `--output OUTPUT` | 可选 | 输出文件名；缺省按剧本 `novel.chapter` 字段生成。无论何种取值，最终都落在 `output/` 子目录内 |
| `--music MUSIC` | 可选 | BGM 文件路径（相对项目 cwd 或绝对路径），但**必须解析后位于项目目录内** |
| `--no-transitions` | flag | 全部用 cut 直接拼接，忽略剧本里的 `transition_to_next` |

## 工作流程

1. **读剧本** — 通过 `ProjectManager.load_script()` 从 `scripts/` 加载（路径过滤复用 lib 内 `_safe_subpath`）
2. **收集片段** — 按 `scenes[i].generated_assets.video_clip` 逐个解析视频文件并校验存在
3. **拼接** — 默认走 normalize → concat（先把每段规范化为统一 H.264/AAC，再用 concat filter 编码），有 `xfade` 转场需求时按 `transition_to_next` 加滤镜
4. **混音** — 若指定 `--music`，再做一遍 audio mix；输出文件名追加 `_with_music`

## 支持的转场类型

按剧本字段 `scenes[i].transition_to_next` 映射：

| 字段值 | ffmpeg 行为 |
|---|---|
| `cut`（默认） | 直接拼接，无淡入淡出 |
| `fade` | `xfade=transition=fade:duration=0.5` |
| `dissolve` | `xfade=transition=dissolve:duration=0.5` |
| `wipe` | `xfade=transition=wipeleft:duration=0.5` |

## 前置检查

- [ ] 当前 cwd 是项目根（含 `project.json`）
- [ ] 剧本 content_mode 为 drama（顶层有 `scenes[]`）
- [ ] 每个场景的 `generated_assets.video_clip` 都已生成
- [ ] `ffmpeg` / `ffprobe` 都在 PATH（脚本会预检）
- [ ] BGM 文件存在（如指定 `--music`）

## 限制 / 缺失能力

下列能力**未实现**，请使用 Web 端剪映草稿导出：

- 旁白/解说、广告/短片与参考生视频（脚本只识别 `scenes[]`）
- 多集合并 / 单集分片裁剪
- BGM 音量调节、独立 BGM 时间轴
- 片头片尾 intro/outro
- 字幕渲染
