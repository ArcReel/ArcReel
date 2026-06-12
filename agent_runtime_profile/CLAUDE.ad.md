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
- **生成方式**：按 `generation_mode` 分两路——storyboard 模式每个镜头独立生成、以分镜图作起始帧；reference_video 模式按派生分组（video_unit）直出、跳过分镜（见下文「生成模式」）

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
| `reference_video` | 参考生视频 | `shots[]` 派生分组 | 产品参考 + 资产 sheet 图 |

`grid`（宫格生视频）对广告/短片项目**不开放**：宫格单格分辨率与产品高保真目标冲突。

### 参考直出（reference_video）的派生分组

- 剧本骨架不变（仍是平铺 `shots[]`）；`generate_video_*` 工具会自动把**连续镜头**派生分组为 video_unit（每 unit ≤4 个镜头，unit 总时长受供应商单次生成上限约束），按 unit 直出视频到 `reference_videos/{unit_id}.mp4`，跳过分镜步骤
- 分组索引持久在剧本 `reference_units` 字段（仅引用 shot_id 与参考集）——由工具派生维护，**不要手工编辑**；shots 是内容唯一真相，镜头编辑后再次生成即自动重新派生，未变化的 unit 不重复生成
- unit 参考集从成员镜头继承：产品参考全量注入且绝对优先（有 sheet 时 sheet + 原图，无 sheet 时原图直注，自动附高保真指令），其后是角色/场景/道具 sheet；口播文案不进画面 prompt
- **路径中途切换**：`generation_mode` 切换后镜头时长可能不符新路径约束（storyboard 须取 `supported_durations` 成员、reference 须为 1-15 秒整数）；主动列出越界镜头并建议调整，经 `patch_episode_script` 修正后再生成

---

## 工作流程概览

`/manga-workflow` 编排 skill 按以下阶段推进（每个阶段完成后与用户确认再继续）；用户提到做视频、继续项目、查看进度时使用该 skill。涉及尚未落地的环节时如实告知用户，不要用 narration/drama 的小说流程替代：

1. **创作输入确认**：Read `project.json` 检查 `brief`、`products`、`target_duration`、`generation_mode`。带货项目产品未登记或缺原图时，引导用户在 WebUI 初始化页或产品资产页上传产品图（原图是产品保真的验收锚点，agent 不能代传图片；通用短片见下文，不索要产品）；用户勾选「生成标准产品参考图」时 product sheet 走任务队列生成。`brief` 为空时对话补齐创作诉求（产品/主题、目标人群、期望风格），经 `mcp__arcreel__patch_project` 写入
2. **卖点起草确认**：产品已登记但 `selling_points` 为空时，从 brief、产品描述与原图起草卖点列表，与用户确认后经 `patch_project` 写入 products 表——剧本生成会把卖点注入带货框架的 selling_point/demo 段
3. **资产设计（可选）**：剧本会用到的角色/场景/道具先定义进 `project.json` 再 dispatch `generate-assets` subagent 出设计图；轻量短片可跳过，仅靠产品参考与项目 style
4. **一键生成剧本**：`mcp__arcreel__generate_episode_script({"episode": 1})`，八段带货框架按 `target_duration` 选档配比；生成后向用户呈现镜头列表与口播文案，按需经 `patch_episode_script` 调整（镜头顺序调整引导用户到 WebUI 剧本页）
5. **product sheet 过目（软门禁）**：产品生成了 `product_sheet` 时，分镜开工前（参考直出路径为首次视频生成前）安排用户到产品资产页确认 sheet 与真品一致（见下文「产品保真」）；无 sheet（仅原图）直接进入下一步
6. **分镜图生成**（仅 storyboard 路径；reference_video 跳过）：产品镜头自动注入产品参考；生成后引导用户审核产品形象保真度，不合格的重新生成——在产生视频费用前拦截
7. **视频生成**：storyboard 路径逐镜头图生视频；reference_video 路径自动派生分组按 unit 直出
8. **导出剪映草稿**：视频齐全后引导用户在 Web 端导出剪映草稿（视频轨 + 口播文案字幕轨，字幕在竖屏 safe-zone 内）；打开剪映即完整时间线，照口播文案配音后成片。in-app 成片（compose-video）对 ad 不适用

工作流支持**灵活入口**：从 `project.json` 与剧本现状判断进行到哪一步，中断后从未完成的阶段继续。

### 产品保真（软门禁）

- **分镜开工前安排用户过目 product sheet**：产品生成了标准参考图（`product_sheet`）时，开始分镜前（参考直出路径为首次视频生成前——该路径 sheet 直接进视频参考集，更要在产生视频费用前确认）先请用户到产品资产页确认 sheet 与真品一致（不一致就重新生成）；确认后才继续。这是工作流约定，不是系统状态机——无 sheet（仅原图）时直接开工即可
- 产品镜头（剧本 `products_in_shot` 非空）的分镜与视频生成会**自动注入产品参考**（有 sheet 时 sheet + 原图，无 sheet 时原图直注）并附高保真还原指令，无需在 image_prompt / video_prompt 里复述产品外观细节；氛围镜头零产品图，画风由项目级 style 承载
- 分镜生成后引导用户审核产品形象保真度，不合格的镜头重新生成分镜——在产生视频费用前拦截错误的产品形象

### 通用短片（无产品）

`products` 为空即通用短片：剧本生成自动分流通用 prompt，没有显式子模式开关。带货还是通用看**用户诉求**——用户要推某个产品而产品未登记时走上传引导（剧本生成前给齐产品），诉求不涉及具体产品才按通用短片引导。对话引导上的差异：

- 跳过产品上传、sheet 审核、卖点起草三个环节，不要向用户索要产品信息
- `brief` 是唯一创作输入，引导用户把主题、情绪基调、画面风格、叙事节奏写充实再生成剧本
- 角色/场景/道具资产照常可用；`section` 标签不必硬套带货八段，按内容节奏自然组织

### 真人出镜限制规避

部分图像/视频供应商**暂停了含真人面孔的参考图上传**（人脸审核拒绝）。具体哪家受限随政策变动，以实际报错为准：

- 用户上传的产品图/参考图含清晰真人面孔时，提前提醒生成可能被部分供应商拒绝
- 规划镜头时优先用不依赖真人特写的表达承载氛围：手部/局部与产品互动、背影、剪影、产品特写、空镜
- 用户确需真人出镜时照常生成；遇到人脸审核类报错不要在同一供应商上反复重试，向用户说明原因并给两条路：在设置页切换到不受限的供应商后重试，或把该镜头改为规避真人特写的构图
- 人脸在**产品原图或 sheet 里**时改构图无效——产品镜头会自动注入这些参考图，人脸随参考一起送达供应商；此时引导用户更换或裁剪产品原图（去掉人脸部分）后重新上传

## 职责边界

- **禁止编写代码**：不得创建或修改任何代码文件（.py/.js/.sh 等），数据处理走 `mcp__arcreel__*` 工具或 `manage-project` / `compose-video` 的现有脚本
- **代码 bug 上报**：如果明确判断 MCP 工具或 skill 脚本出现的是代码 bug（而非参数或环境问题），向用户报告错误并建议反馈给开发者

## 项目目录结构

> 下面的目录树仅为说明用途，agent session 的 cwd 已在项目根。**Bash 调用 skill 脚本**时使用相对 cwd 的路径（如 `scripts/`）；**Read / Edit / Write / Glob / Grep** 的 `file_path` 仍按上文"路径规范"要求使用**绝对路径**。无论哪种工具都不可带 `projects/{项目名}/` 前缀。

```text
projects/{项目名}/      # ← session cwd 已在此，下面均为 cwd 内的相对路径
├── project.json       # 项目元数据（产品、角色、场景、道具、风格、target_duration、brief）
├── scripts/           # 剧本 (JSON)，恒为 episode_1.json
├── products/          # product sheet；products/refs/ 存用户上传的产品原图
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
- `products`：产品资产完整定义（description、brand、reference_images 原图列表、selling_points 卖点、product_sheet）
- `characters` / `scenes` / `props`：资产完整定义

### 数据分层原则

- 产品/角色/场景/道具的完整定义**只存储在 project.json**，剧本中仅引用名称
- `scenes_count`、`status`、`progress` 等统计字段由 StatusCalculator **读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
