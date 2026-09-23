---
name: generate-script
description: 提示词编写：调用项目配置的文本模型，为正式脚本中待编写的分镜 / 视频单元补出 image_prompt 与 video_prompt（参考生视频改写单元正文）；ad 项目尚无正式脚本时整份生成。由 create-episode-script 子智能体调用，输入是正式脚本自身的内容与 project.json。
user-invocable: false
---

# generate-script

调用项目配置的文本生成模型（Gemini / Ark / OpenAI / 自定义供应商，由 project.json 决定），
为正式脚本中待编写的条目补出视觉层。剧本里的 `image_prompt` / `video_prompt`
是后续图像 / 视频生成的"种子"，**Prompt 质量基本决定了画面质量**——所以本 skill 是
ArcReel 整条 pipeline 中最值得重点优化的一环。

## 前置条件

1. 项目目录下存在 `project.json`（含 style / overview / characters / scenes / props）
2. **正式脚本 `scripts/episode_N.json` 已存在**（drama / narration / reference_video）：内容确认即把脚本规划整集转为正式脚本，全部条目带待编写标记。确认有两条等价路径：用户在 Web 端点击确认，或在对话中明确同意后由主 Agent 调用 `mcp__arcreel__confirm_script_review({"episode": N})`。本工具只读正式脚本、不读脚本规划：脚本规划缺失或重跑后尚未确认都不影响编写。
   - **ad（广告/短片）**：尚无正式脚本时本工具按 `project.json` 的 `brief` + `products`（含 selling_points）+ `target_duration` 整份生成（后端按审定的带货八段框架配比表构建 prompt，`products` 为空自动分流通用短片）；已有正式脚本时与其他路线一样只编写待编写条目。
3. **约束失败产出保留为待修复草稿，不丢弃重抽**：参考生视频提示词编写的产出违反内容约束时，正式文件不写，产出连同逐条违约报告落到 `*.invalid.json`。用 `open_draft` 读取草稿及 revision，按 `violations[]` 修复完整 `content`，再用 `patch_draft` 提交；随后以相同 `episode` 与 `doc_type: reference_prompt_authoring` 调 `promote_draft`，仍违约则继续 open → patch → promote，无轮次上限。

## 用法

通过 MCP 工具调用（项目名由 session 绑定，不需要传）：

```text
mcp__arcreel__generate_episode_script({"episode": N})
mcp__arcreel__generate_episode_script({"episode": N, "instructions": "<附加指令原文，可选，无则省略>"})
mcp__arcreel__generate_episode_script({"episode": N, "dry_run": true})   # 仅预览 prompt
```

输出路径由工具内部固定为 `{project}/scripts/episode_{N}.json`，不支持自定义；
如需重命名或归档，请在 Web 端操作。

### 补充提示词（`author_prompts`）

正式脚本里带待编写标记的条目（内容确认转换出的全部条目、手动新增的分镜 / 单元）还没有视觉层。
工作流计划的 `next_action.type` 为 `author_prompts` 时，`requested_ids` 就是这些待编写条目。
不传 `entry_ids` 调用即编写全部待编写条目，写回后标记清除：

```text
mcp__arcreel__generate_episode_script({"episode": N})
```

- 已有视觉层、无待编写标记的条目保持原样，其中包括用户手写的提示词。
- **`entry_ids` 是显式重写**：它覆盖所列条目已有的视觉层（参考生视频改写单元正文），只在用户明确要求
  重写这几条时传入；内容字段（台词、旁白正文、对应原文）与备注、已生成产物照常保留。
- 没有待编写条目时工具不调用模型、不改剧本，回执会说明。整集重做走重跑脚本规划并重新确认；
  ad 要整份重做须先移除正式脚本。

**重要：生成剧本必须调用上述 MCP 工具。此 skill 不提供任何 Python/Shell 脚本，不得用 BASH 调 `python .../scripts/*.py`。**

## 生成流程

MCP 工具内部通过 `ScriptGenerator` 完成以下步骤：

1. **加载 project.json** — 读取 content_mode、characters、scenes、props、overview、style
2. **加载正式脚本** — 取本次要编写的条目（待编写条目，或 `entry_ids` 点名的条目）；ad 尚无正式脚本时整份生成
3. **构建 Prompt** — 由 `lib.prompts.prompt_builders_script`、`lib.prompts.prompt_builders_reference` 或 `lib.prompts.prompt_builders_ad` 生成，输入是这些条目的内容字段
4. **调用 TextBackend** — 由 `TextGenerator` 按项目配置选择文本模型，传入 Pydantic schema 作为 `response_schema` 强约束 JSON 结构
5. **Pydantic 验证与写回** — LLM 只产出视觉层，后端按条目 id 写回正式脚本，内容字段不进 LLM 输出，从工程上杜绝其经 Structured Outputs 漂移：
   - narration → `NarrationVisualEpisodeScript`（`segment_id` + image_prompt + video_prompt）
   - drama（storyboard，含 grid_storyboard）→ `DramaVisualScript`（`scene_id` + image_prompt + video_prompt）
   - ad 分镜 → `AdVisualScript`（`shot_id` + image_prompt + video_prompt）；ad 整份生成 → `AdEpisodeScript`（storyboard）或 `AdReferenceFlatScript`（reference_video）
   - reference_video → `ReferencePromptAuthoringFlatScript`：待编写单元按顺序各一段改写后的正文，台词逐字保留
6. **补充元数据** — `episode`、`content_mode`、`novel`（项目 title + `第N集`）、时间戳。这些字段对 LLM 隐藏（SkipJsonSchema），由后端从 `project.json` 注入，避免 LLM 幻觉污染下游消费方（compose-video 的 mp4 文件名、剪映草稿等）。
   - 注：**任何骨架的剧本都不写入顶层 `generation_mode`**。生成模式是项目级事实（`project.json` 的 `generation_mode`，创建时锁定），剧本骨架种类本身即生成模式的体现；消费方一律读 `project.json` 分派，不得从剧本上找该字段。

## 输出格式

生成的 JSON 文件保存至 `scripts/episode_N.json`，核心结构：

- `title`：LLM 写入的剧集标题
- `episode` / `content_mode` / `novel`（含 title、chapter）：由后端 `_add_metadata` 注入，不依赖 LLM 输出
- 旁白/解说：`segments[]`（每个分镜含 novel_text、duration_seconds、segment_break、出场角色 / 场景 / 道具 —— 内容确认时从脚本规划转入，之后在正式脚本上修改；image_prompt、video_prompt —— 由 prompt_authoring 生成）
- 剧情演绎：`scenes[]`（每个分镜含 image_prompt、video_prompt、duration_seconds，以及内容确认时从脚本规划转入的 utterances、source_text、characters_in_scene 等）
- 广告/短片：`shots[]`（每个分镜含 section、voiceover_text、products_in_shot、image_prompt、video_prompt、duration_seconds 等）；总时长偏离 `target_duration` 超阈值仅日志提醒，不阻塞保存
- 参考生视频：`video_units[]`（每个视频单元含 `text`、`duration_seconds` 等）
- `metadata`：created_at、updated_at、generator

条目数与全集总时长不落盘：它们逐读剧本即得，由项目摘要读时计算，落一份只会与正文漂移。

## `--dry-run` 输出

打印将发送给文本模型的完整 prompt 文本，不调用 API、不写文件。用于检查 prompt 质量和长度。

> 两种生成模式（storyboard / reference_video）在 narration / drama 下的数据路径、脚本规划子智能体、schema 选择详见 `.claude/references/generation-modes.md`；ad 的路径见 `CLAUDE.ad.md`。
