# AI 视频生成工作空间
<!-- mode: ad -->

---

## 重要总则

以下规则适用于整个项目的所有操作：

### 视频规格
- **视频比例**：由项目 `aspect_ratio` 配置决定（广告/短片默认 9:16 竖屏），无需在 prompt 中指定
- **时长规划**：广告/短片项目**没有** `default_duration` 偏好，按项目 `target_duration`（目标总时长，秒）规划
  - 分镜图生视频：单分镜时长必须取所选视频模型 `supported_durations` 中的值；子任务运行时通过 `mcp__arcreel__get_video_capabilities` 工具自查真值
  - 参考生视频：每个 video unit 持有符合剧本模型结构约束的正整数编排时长，unit 内不单列分镜时长；生成预检会把编排时长投影到供应商申请档位
- **图片分辨率**：1K
- **视频分辨率**：1080p
- **生成方式**：按 `generation_mode` 分两路——分镜图生视频中每个分镜独立生成、以分镜图作起始帧；参考生视频按自包含 video unit 直出、跳过分镜（见下文「生成模式」）

> **关于 extend 功能**：Veo 3.1 extend 功能仅用于延长单个镜头，
> 每次固定 +7 秒，不适合用于串联不同镜头。不同镜头之间使用 ffmpeg 拼接。

### 音频规范
- **BGM 自动禁止**：生成端已在视频 prompt 末尾自动追加「禁止出现：BGM、文字字幕、水印」，无需手动追加，video_prompt 里也不要描述 BGM / 配乐

### 视频 prompt 措辞

- **避开任务类型触发词**：`video_prompt` 里不要用「增加 / 删除 / 去掉 / 修改 / 替换 / 改成 / 延长 / 续写」这类祈使动词。部分模型（如 Seedance 2.5）按 prompt 措辞判定任务类型，带这些词会把参考生视频误判成视频编辑或视频延长，而误判在异步生成阶段才报错——任务已排队、已计费。改成直接描述目标画面本身：不写「把包装换成新版」，写「桌面上摆着新版包装」

### 工具调用

- **业务入队 / 文本生成 / 能力查询**：统一走 `mcp__arcreel__*` 系列 SDK in-process MCP tool（角色/场景/道具/分镜/视频/宫格/集脚本/规范化剧本/视频能力查询）。它们跑在 server 主进程，不受 sandbox 网络白名单约束，智能体直接以 tool 形式调用。
- **编辑项目 JSON**：修改剧本（`scripts/*.json`）或角色/场景/道具（`project.json`）**一律走 `mcp__arcreel__*` 编辑工具**——批量改剧本时先调用 `get_episode_script_revision`，再把其 revision 原样作为 `patch_episode_script` 的 `expected_revision`，并传有序 `operations[]`（`update` / `insert_after` / `move_after` / `remove`）；整批先预检后原子提交，失败结果用 `operation_index` 与 field location 定位，revision 冲突时重新读取再重做。改分集标题用 `patch_episode_meta`，增/删/拆分镜的便捷工具也委托同一事务编辑器；角色/场景/道具用 `patch_project`。**严禁**用 Write / Edit / Bash 直改这两类文件（已被 sandbox `denyWrite` 与 PreToolUse hook 双层拒绝）。**改 prompt 必重生**：用 `patch_episode_script` 改了某些分镜的 `image_prompt` / `video_prompt` 后，工具不会自动作废旧图/视频，必须紧接着调对应生成工具重新生成这些分镜，否则会留下「新 prompt + 旧画面」的陈旧。
- **Bash 用途**：仅供通用排查与文件浏览（`ls / cat / jq / python / curl` 等），以及 `manage-project` / `compose-video` 这两个 skill 内还保留的 Python 脚本。
- **敏感文件保护**：`.env` / `vertex_keys/` / `.system_config.json*` / `.arcreel.db*` / `.claude/settings.json` 由 sandbox profile（`filesystem.denyRead`）内核级拒绝读取，并由 PreToolUse 文件访问 hook 双重防御；代码文件（.py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml）受运行时 hook 阻止写入。

### 路径规范

智能体 session 的当前工作目录（cwd）已绑定到当前项目根，**所有工具参数中的路径必须遵循以下规则**：

- **Read / Edit / Write / Glob / Grep**：`file_path` 使用**绝对路径**
- **Bash 调用 skill 脚本**：使用**相对项目根 cwd** 的路径，例如：
  - ✅ `scripts/episode_1.json`、`storyboards/E1S01.png`
  - ❌ `projects/{项目名}/scripts/episode_1.json`（双前缀，占位符替换或拼接出错就会落到 projects 根）
- **严禁**在工具参数中出现 `projects/{...}/` 前缀；该前缀仅用于文档说明项目目录结构，**不可直接作为参数传给任何工具**
- skill 脚本内部已加 cwd 校验，cwd 漂离当前项目目录时会直接拒绝执行
- **`.claude/agents/*.md` / `SKILL.md` 中的相对形式**：子任务指引（如「读取 `project.json`」）里出现的相对路径是**项目内位置说明**，并非可直接传给工具的 `file_path` 值。调用 Read/Edit/Write/Glob/Grep 时仍按本节规则用 session cwd 拼成绝对路径再传参

---

## 创作类型

本项目为**广告/短片**（ad），产出**单个**约 `target_duration` 秒的短视频，而非多集系列：

- storyboard 路径的剧本是平铺 `shots[]`，`shot_id` 格式 `E1S{n}`；每个镜头携带 `section`（带货框架段落标签，如 hook/pain_point/product_reveal/selling_point/demo/trust/price_promo/cta）与一等口播文案 `voiceover_text`
- reference_video 路径的剧本是自包含 `video_units[]`；每个 unit 持有引用语法正文、编排时长与产物，不持久化 `section`、`voiceover_text` 或 `speech_mode`；参考图不落盘，执行期从正文派生
- 项目**恒单集**：`episodes` 恒为第 1 集单条，剧本即 `scripts/episode_1.json`；**不存在分集概念**，不要做分集规划或拆分
- 创作输入为 `project.json` 顶层的 `brief`（创作诉求短文本）与 `target_duration`（目标总时长，秒）；不走小说源文件导入流程
- 剧本总时长应贴近 `target_duration`，偏差过大时提醒用户而非拒绝保存

> 生成模式（storyboard / reference_video）由 `project.json` 顶层 `generation_mode` 字段唯一决定，项目创建后不可更改；与创作类型独立。ad 的数据结构与阶段分支以本文为准——`.claude/references/generation-modes.md` 只覆盖 narration / drama 的内容整理与 schema 路径，不适用于 ad。

---

## 生成模式

广告/短片的**生成模式**（`generation_mode`）由 `project.json` 顶层字段唯一表达，创建后不可更改，不存在集级覆盖：

| generation_mode | 名称（UI） | 数据主结构 | 视觉参考来源 |
|---|---|---|---|
| `storyboard` | 分镜图生视频 | `shots[]` + 分镜图 | 每镜头一张分镜图作起始帧 |
| `reference_video` | 参考生视频 | 自包含 `video_units[]` | 正文 `@[名称]` 提及派生的资产图（无 sheet 时退到原图） |

宫格装配（`grid_storyboard`）对广告/短片项目**不开放**：宫格单格分辨率与商品高保真目标冲突。

### 参考生视频（reference_video）的自包含单元

- 剧本生成会单阶段直接产出 `video_units[]`，不创建需要内容确认的 step1 中间态；每个 unit 对应一次生成调用与 `reference_videos/{unit_id}.mp4`
- unit 正文是一段自由文本，使用统一引用语法：`@[角色]{台词}` 表达角色发声，`{台词}` 表达无归属旁白，两者可写在行内任意位置；商品、角色、场景、道具均用 `@[名称]` mention。参考图由系统在执行期按首次提及顺序从正文解析，同名按 product → character → scene → prop 归属
- 一个 unit 只能承载角色发声、无归属旁白或无人声中的一种；需要切换发声归属时在规划阶段拆成相邻 unit。标记 `needs_replan` 的存量问题单元须先重新规划，生成入口会拒绝入队
- 参考集按正文首次 mention 顺序排列，商品与角色/场景/道具同规则：每件资产有 sheet 用 sheet，没有才退到它的全部原图；不按类型排序，也不在有 sheet 时额外注入原图
- **时长约束**：每个 unit 的 `duration_seconds` 是符合剧本模型结构约束的正整数编排时长，所有 unit 之和应贴近 `target_duration`；供应商档位由生成预检处理，不在剧本规划时量化

---

## 工作流程概览

`/video-workflow` 编排 skill 按服务端计划推进（每个动作完成后与用户确认再继续）；用户提到做视频、继续项目、查看进度时使用该 skill。涉及尚未落地的环节时如实告知用户，不要用 narration/drama 的小说流程替代。

**步骤表不在这里，也不在 skill 里**：调用 `mcp__arcreel__get_workflow_plan` 取回 `steps[]` 与唯一的 `next_action`，照它路由。受控动作表、旁白交付、整批准入判定与状态轴读法见 `.claude/references/workflow-plan.md`。

需要在这里说清、不由计划表达的 ad 专属规则：

- **创作输入**：带货项目商品未登记或缺原图时，引导用户在 WebUI 初始化页或商品资产页上传商品图（原图是商品保真的验收锚点，智能体不能代传图片；通用短片见下文，不索要商品）；用户勾选「生成标准商品参考图」时 product sheet 走任务队列生成。`brief` 为空时对话补齐创作诉求（商品/主题、目标人群、期望风格），经 `mcp__arcreel__patch_project` 写入
- **生成模式**：用户中途要求更改生成模式（storyboard ↔ reference_video）时明确告知生成模式创建后不可更改，无绕过方式；宫格装配对 ad 不开放
- **卖点**：商品已登记但 `selling_points` 为空时，从 brief、商品描述与原图起草卖点列表，与用户确认后经 `patch_project` 写入 products 表——剧本生成会把卖点注入带货框架的 selling_point/demo 段
- **资产设计（可选）**：剧本会用到的角色/场景/道具先定义进 `project.json` 再 dispatch `generate-assets` 子任务出资产图；轻量短片可跳过，仅靠商品参考与项目 style
- **剧本**：`mcp__arcreel__generate_episode_script({"episode": 1})` 单阶段产出，八段带货框架按 `target_duration` 选档配比；storyboard 路径向用户呈现镜头列表与口播文案，reference_video 路径呈现 video unit 列表与引用语法正文，按需经 `patch_episode_script` 调整（顺序调整引导用户到 WebUI 剧本页）
- **product sheet 过目（软门禁）**：商品生成了 `product_sheet` 时，分镜开工前（参考生视频路径为首次视频生成前）安排用户到商品资产页确认 sheet 与真品一致（见下文「商品保真」）；无 sheet（仅原图）直接进入下一步
- **保真拦截**：分镜图生成后引导用户审核商品形象保真度，不合格的重新生成——在产生视频费用前拦截
- **导出**：视频齐全后引导用户在 Web 端导出剪映草稿。声音归属与字幕时序由服务端 presentation 结果决定，预览、下载与剪映草稿消费同一份；智能体不自行估算字幕时序、不静音供应商原音、也不替用户判断 TTS 是否必需。stale 产物照常可导出，导出不清空也不覆盖旧付费媒体。in-app 成片（compose-video）对 ad 不适用

工作流支持**灵活入口**：计划自动定位到第一个未完成的动作，中断后从那里继续。

### 商品保真（软门禁）

- **分镜开工前安排用户过目 product sheet**：商品生成了标准参考图（`product_sheet`）时，开始分镜前（参考生视频路径为首次视频生成前——该路径 sheet 直接进视频参考集，更要在产生视频费用前确认）先请用户到商品资产页确认 sheet 与真品一致（不一致就重新生成）；确认后才继续。这是工作流约定，不是系统状态机——无 sheet（仅原图）时直接开工即可
- 商品镜头（剧本 `products_in_shot` 非空）在 storyboard 路径的**分镜生成**会**自动注入商品参考**（有 sheet 时 sheet + 原图，无 sheet 时原图直注）并附高保真还原指令，无需在 image_prompt 里复述商品外观细节；该路径的**视频生成**不再叠加商品参考图，商品一致性由分镜图承载。reference_video 路径跳过分镜，参考图按正文提及顺序在执行期派生、商品不排最前（见上文「参考生视频（reference_video）的自包含单元」）。氛围镜头零商品图，画风由项目级 style 承载
- 分镜生成后引导用户审核商品形象保真度，不合格的镜头重新生成分镜——在产生视频费用前拦截错误的商品形象

### 通用短片（无商品）

`products` 为空即通用短片：剧本生成自动分流通用 prompt，没有显式子模式开关。带货还是通用看**用户诉求**——用户要推某个商品而商品未登记时走上传引导（剧本生成前给齐商品），诉求不涉及具体商品才按通用短片引导。对话引导上的差异：

- 跳过商品上传、sheet 审核、卖点起草三个环节，不要向用户索要商品信息
- `brief` 是唯一创作输入，引导用户把主题、情绪基调、画面风格、叙事节奏写充实再生成剧本
- 角色/场景/道具资产照常可用；`section` 标签不必硬套带货八段，按内容节奏自然组织

### 真人出镜限制规避

部分图像/视频供应商**暂停了含真人面孔的参考图上传**（人脸审核拒绝）。具体哪家受限随政策变动，以实际报错为准：

- 用户上传的商品图/参考图含清晰真人面孔时，提前提醒生成可能被部分供应商拒绝
- 规划镜头时优先用不依赖真人特写的表达承载氛围：手部/局部与商品互动、背影、剪影、商品特写、空镜
- 用户确需真人出镜时照常生成；遇到人脸审核类报错不要在同一供应商上反复重试，向用户说明原因并给两条路：在设置页切换到不受限的供应商后重试，或把该镜头改为规避真人特写的构图
- 人脸在**商品原图或 sheet 里**时改构图无效——商品镜头的分镜生成（storyboard 路径）与 reference_video 路径的视频生成都会自动注入这些参考图，人脸随参考一起送达供应商（storyboard 路径的视频生成本身不再收商品参考，不受此限）；此时引导用户更换或裁剪商品原图（去掉人脸部分）后重新上传

## 职责边界

- **禁止编写代码**：不得创建或修改任何代码文件（.py/.js/.sh 等），数据处理走 `mcp__arcreel__*` 工具或 `manage-project` / `compose-video` 的现有脚本
- **代码 bug 上报**：如果明确判断 MCP 工具或 skill 脚本出现的是代码 bug（而非参数或环境问题），向用户报告错误并建议反馈给开发者

## 项目目录结构

> 下面的目录树仅为说明用途，智能体 session 的 cwd 已在项目根。**Bash 调用 skill 脚本**时使用相对 cwd 的路径（如 `scripts/`）；**Read / Edit / Write / Glob / Grep** 的 `file_path` 仍按上文「路径规范」要求使用**绝对路径**。无论哪种工具都不可带 `projects/{项目名}/` 前缀。

```text
projects/{项目名}/      # ← session cwd 已在此，下面均为 cwd 内的相对路径
├── project.json       # 项目元数据（商品、角色、场景、道具、风格、target_duration、brief）
├── scripts/           # 剧本 (JSON)，恒为 episode_1.json
├── products/          # product sheet；products/refs/ 存用户上传的商品原图
├── characters/        # 角色资产图
├── scenes/            # 场景资产图
├── props/             # 道具资产图
├── storyboards/       # 分镜图（分镜图生视频）
├── videos/            # 生成的视频片段（分镜图生视频）
├── reference_videos/  # 生成的 video_unit（参考生视频）
├── thumbnails/        # 首帧缩略图
└── output/            # 最终输出
```

### project.json 核心字段

- `schema_version`：项目数据格式版本
- `title`、`content_mode`（固定 `ad`）、`generation_mode`（`storyboard`/`reference_video`，创建后不可更改）、`style`、`style_description`
- `target_duration`：目标总时长（秒，正整数）
- `brief`：创作诉求短文本（可为空）
- `episodes`：恒为第 1 集单条（episode、title、script_file）
- `products`：商品资产完整定义（description、brand、reference_images 原图列表、selling_points 卖点、product_sheet）
- `characters` / `scenes` / `props`：资产完整定义

### 数据分层原则

- 商品/角色/场景/道具的完整定义**只存储在 project.json**，剧本中仅引用名称
- 项目摘要 `episodes[]` 的 `item_count`（分镜数 / 视频单元数）、`status`、产物计数等派生字段由项目摘要**读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
