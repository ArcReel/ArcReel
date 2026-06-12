# AI 视频生成工作空间
<!-- mode: ad -->

---

## 重要总则

以下规则适用于整个项目的所有操作：

### 视频规格
- **视频比例**：由项目 `aspect_ratio` 配置决定（广告/短片默认 9:16 竖屏），无需在 prompt 中指定
- **单镜头时长**：广告/短片项目**没有** `default_duration` 偏好——镜头时长按项目 `target_duration`（目标总时长，秒）逐镜头规划
  - storyboard 模式：单镜头时长必须取所选视频模型 `supported_durations` 中的值；subagent 运行时通过 `mcp__arcreel__get_video_capabilities` 工具自查真值
  - reference_video 模式：单镜头时长为 1-15 秒自由整数，不受供应商 `supported_durations` 限制（短切节奏赖此成立）
- **图片分辨率**：1K
- **视频分辨率**：1080p
- **生成方式**：每个镜头独立生成，使用分镜图作为起始帧

> **关于 extend 功能**：Veo 3.1 extend 功能仅用于延长单个镜头，
> 每次固定 +7 秒，不适合用于串联不同镜头。不同镜头之间使用 ffmpeg 拼接。

### 音频规范
- **BGM 自动禁止**：在视频 prompt 末尾统一追加"禁止出现：BGM、文字字幕、水印"

### 工具调用

- **业务入队 / 文本生成 / 能力查询**：统一走 `mcp__arcreel__*` 系列 SDK in-process MCP tool（角色/场景/道具/分镜/视频/宫格/集脚本/规范化剧本/视频能力查询）。它们跑在 server 主进程，不受 sandbox 网络白名单约束，agent 直接以 tool 形式调用。
- **编辑项目 JSON**：修改剧本（`scripts/*.json`）或角色/场景/道具（`project.json`）**一律走 `mcp__arcreel__*` 编辑工具**——剧本改字段用 `patch_episode_script`，改分集标题用 `patch_episode_meta`，增/删/拆分镜用 `insert_segment` / `remove_segment` / `split_segment`，角色/场景/道具用 `patch_project`。**严禁**用 Write / Edit / Bash 直改这两类文件（已被 sandbox `denyWrite` 与 PreToolUse hook 双层拒绝）。**改 prompt 必重生**：用 `patch_episode_script` 改了某分镜的 `image_prompt` / `video_prompt` 后，工具不会自动作废旧图/视频，必须紧接着调对应生成工具重新生成该分镜，否则会留下「新 prompt + 旧画面」的陈旧。
- **Bash 用途**：仅供通用排查与文件浏览（`ls / cat / jq / python / curl` 等），以及 `manage-project` / `compose-video` 这两个 skill 内还保留的 Python 脚本。
- **敏感文件保护**：`.env` / `vertex_keys/` / `.system_config.json*` / `.arcreel.db*` / `.claude/settings.json` 由 sandbox profile（`filesystem.denyRead`）内核级拒绝读取，并由 PreToolUse 文件访问 hook 双重防御；代码文件（.py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml）受运行时 hook 阻止写入。

### 路径规范

agent session 的当前工作目录（cwd）已绑定到当前项目根，**所有工具参数中的路径必须遵循以下规则**：

- **Read / Edit / Write / Glob / Grep**：`file_path` 使用**绝对路径**
- **Bash 调用 skill 脚本**：使用**相对项目根 cwd** 的路径，例如：
  - ✅ `scripts/episode_1.json`、`storyboards/E1S01.png`
  - ❌ `projects/{项目名}/scripts/episode_1.json`（双前缀，占位符替换或拼接出错就会落到 projects 根）
- **严禁**在工具参数中出现 `projects/{...}/` 前缀；该前缀仅用于文档说明项目目录结构，**不可直接作为参数传给任何工具**
- skill 脚本内部已加 cwd 校验，cwd 漂离当前项目目录时会直接拒绝执行
- **关于 agent.md / SKILL.md 中的相对形式**：subagent 指引（如「读取 `project.json`」）里出现的相对路径是**项目内位置说明**，并非可直接传给工具的 `file_path` 值。调用 Read/Edit/Write/Glob/Grep 时仍按本节规则用 session cwd 拼成绝对路径再传参

---

## 内容模式

本项目为**广告/短片模式**（ad），产出**单个**约 `target_duration` 秒的短视频，而非多集系列：

- 剧本数据结构为平铺 `shots[]`，`shot_id` 格式 `E1S{n}`；每个镜头携带 `section`（带货框架段落标签，如 hook/pain_point/product_reveal/selling_point/demo/trust/price_promo/cta）与一等口播文案 `voiceover_text`（字幕导出与后续配音的唯一来源）
- 项目**恒单集**：`episodes` 恒为第 1 集单条，剧本即 `scripts/episode_1.json`；**不存在分集概念**，不要做分集规划或拆分
- 创作输入为 `project.json` 顶层的 `brief`（创作诉求短文本）与 `target_duration`（目标总时长，秒）；不走小说源文件导入流程
- 剧本总时长应贴近 `target_duration`，偏差过大时提醒用户而非拒绝保存

> 生成模式通过 `project.json` 的 `generation_mode` 字段配置，与内容模式独立。

---

## 生成模式

广告/短片模式仅开放两种**生成模式**（`generation_mode`）：

| generation_mode | 名称（UI） | 数据主结构 | 视觉参考来源 |
|---|---|---|---|
| `storyboard`（默认） | 图生视频 | `shots[]` + 分镜图 | 每镜头一张分镜图作起始帧 |
| `reference_video` | 参考生视频 | `shots[]` 派生分组 | 资产 sheet 图作为参考 |

`grid`（宫格生视频）对广告/短片项目**不开放**：宫格单格分辨率与产品高保真目标冲突。

---

## 工作流程

广告/短片模式的工作流引导（当前可用步骤与边界）见 `manga-workflow` skill；用户提到做视频、继续项目、查看进度时使用该 skill。涉及尚未落地的环节时如实告知用户，不要用 narration/drama 的小说流程替代。

## 职责边界

- **禁止编写代码**：不得创建或修改任何代码文件（.py/.js/.sh 等），数据处理走 `mcp__arcreel__*` 工具或 `manage-project` / `compose-video` 的现有脚本
- **代码 bug 上报**：如果明确判断 MCP 工具或 skill 脚本出现的是代码 bug（而非参数或环境问题），向用户报告错误并建议反馈给开发者

## 项目目录结构

> 下面的目录树仅为说明用途，agent session 的 cwd 已在项目根。**Bash 调用 skill 脚本**时使用相对 cwd 的路径（如 `scripts/`）；**Read / Edit / Write / Glob / Grep** 的 `file_path` 仍按上文"路径规范"要求使用**绝对路径**。无论哪种工具都不可带 `projects/{项目名}/` 前缀。

```text
projects/{项目名}/      # ← session cwd 已在此，下面均为 cwd 内的相对路径
├── project.json       # 项目元数据（角色、场景、道具、风格、target_duration、brief）
├── scripts/           # 剧本 (JSON)，恒为 episode_1.json
├── characters/        # 角色设计图
├── scenes/            # 场景设计图
├── props/             # 道具设计图
├── storyboards/       # 分镜图片（storyboard 模式）
├── videos/            # 生成的视频片段（storyboard 模式）
├── reference_videos/  # 生成的 video_unit（reference_video 模式）
├── thumbnails/        # 首帧缩略图
└── output/            # 最终输出
```

### project.json 核心字段

- `schema_version`：项目数据格式版本
- `title`、`content_mode`（固定 `ad`）、`generation_mode`（`storyboard`/`reference_video`）、`style`、`style_description`
- `target_duration`：目标总时长（秒，正整数）
- `brief`：创作诉求短文本（可为空）
- `episodes`：恒为第 1 集单条（episode、title、script_file）
- `characters` / `scenes` / `props`：资产完整定义

### 数据分层原则

- 角色/场景/道具的完整定义**只存储在 project.json**，剧本中仅引用名称
- `scenes_count`、`status`、`progress` 等统计字段由 StatusCalculator **读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
