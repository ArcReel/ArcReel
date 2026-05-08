---
name: generate-script
description: 调用项目配置的文本模型生成 JSON 剧本（同时产出每个分镜的 image_prompt 与 video_prompt）。由 create-episode-script subagent 调用。读取 step1 中间文件和 project.json，输出符合 Pydantic schema 的剧本。
user-invocable: false
---

# generate-script

调用项目配置的文本生成模型（Gemini / Ark / OpenAI / 自定义供应商，由 project.json 决定），
基于 Step 1 中间文件产出最终的 JSON 剧本。剧本里的 `image_prompt` / `video_prompt`
是后续图像 / 视频生成的"种子"，**Prompt 质量基本决定了画面质量**——所以本 skill 是
ArcReel 整条 pipeline 中最值得重点优化的一环。

## 前置条件

1. 项目目录下存在 `project.json`（含 style / overview / characters / scenes / props）
2. 已完成 Step 1 预处理（按 `effective_mode` 选择一种中间文件）：
   - narration（图生视频 / 宫格生视频 + 说书）：`drafts/episode_N/step1_segments.md`
   - drama（图生视频 / 宫格生视频 + 剧集动画）：`drafts/episode_N/step1_normalized_script.md`
   - reference_video（参考生视频）：`drafts/episode_N/step1_reference_units.md`

## 用法

```bash
# 生成指定剧集的剧本
python .claude/skills/generate-script/scripts/generate_script.py --episode {N}

# 自定义输出路径
python .claude/skills/generate-script/scripts/generate_script.py --episode {N} --output scripts/ep1.json

# 预览 Prompt（不实际调用 API）
python .claude/skills/generate-script/scripts/generate_script.py --episode {N} --dry-run
```

## 生成流程

脚本内部通过 `ScriptGenerator` 完成以下步骤：

1. **加载 project.json** — 读取 content_mode、characters、scenes、props、overview、style
2. **加载 Step 1 中间文件** — 根据 effective_mode 选择对应文件
3. **构建 Prompt** — 由 `lib.prompt_builders_script` 或 `lib.prompt_builders_reference` 生成
4. **调用 TextBackend** — 由 `TextGenerator` 按项目配置选择文本模型，传入 Pydantic schema 作为 `response_schema` 强约束 JSON 结构
5. **Pydantic 验证** — 按 effective_mode 选 schema：
   - reference_video → `ReferenceVideoScript`（含 `video_units[]`）
   - narration → `NarrationEpisodeScript`
   - drama → `DramaEpisodeScript`
6. **补充元数据** — episode、content_mode、统计信息（片段 / 场景 / unit 数、总时长）、时间戳

## Prompt 写作心智

LLM 生成的 `image_prompt` / `video_prompt` 直接决定后续图像 / 视频画质。
让 LLM 写出"可生成"的 prompt，遵循以下原则：

### 1. 结构 = 角色 / 任务 / 上下文 / 字段指引 / 创作目标

不要写松散的"请帮我生成 N 个分镜"。`prompt_builders_script.py` 的模板已经把每段
做了结构化分块（`# 角色与任务` → `# 上下文` → `# 字段写作指引` → `# 创作目标`）。
保持这个骨架，调整内部句子。

### 2. 心智公式：主体 + 动作 + 场景 + 光影 + 镜头 + 风格 + 画质

虽然 schema 把这些拆成了 `image_prompt.scene` / `composition.lighting` /
`composition.shot_type` 等独立字段，写作时心里要有这条"叙事线"——这是爆款分镜
prompt 的隐性骨架。

### 3. 不要重复 schema 已声明的内容

`shot_type` / `camera_motion` / `transition_to_next` 是 `Literal` 枚举，
`response_schema` 已强约束。Prompt 只说"如何选"（如 "情绪极致瞬间宜近景 / 特写"），
不要再列举可选值——浪费 token 且容易和 schema 漂移。

### 4. 字段说明给 example，不写硬性数字限制

LLM 没法精确数字数。"≤200 字"这种约束是无效的——要么过冲要么欠冲。
给 1 个**好例 + 1 个反例**，让模型从示范中泛化节奏。

### 5. 反向约束精简

反向提示词在 CFG（classifier-free guidance）中只有少量项数有效，4-6 项即饱和。
ArcReel 已经收敛到核心 4 项（资产）/ 3 项（视频），由 `lib.prompt_builders` 在拼接时附加。

### 6. 避免标题式段落

不要让 LLM 写「画面基调：...」「光影设定：...」这类 inline 标题，
要求所有元素融为连贯叙述。这是最常见的"AI 味"来源。

## image_prompt / video_prompt 完整示例

下面是一个 drama 模式分镜的目标输出（节选）：

```json
{
  "scene_id": "E1S03",
  "duration_seconds": 6,
  "characters_in_scene": ["林清"],
  "scenes": ["书房"],
  "props": ["信纸"],
  "image_prompt": {
    "scene": "林清坐在窗边木桌前，左手撑着下巴，目光落在桌上一封拆开的信纸上。窗外细雨打在木格窗棂，半边脸笼在蓝灰色的阴影里。",
    "composition": {
      "shot_type": "Medium Close-up",
      "lighting": "左侧木格窗透入的冷蓝色雨日光，色温约 6500K，与桌面右侧低位油灯的暖橙色光形成对照",
      "ambiance": "薄雾从窗缝渗入，灯油的细烟在光柱中翻飞"
    }
  },
  "video_prompt": {
    "action": "林清缓缓抬起头，手指无意识地摩挲信纸边缘，眼角微微收紧；窗外雨势渐大，桌面投下的雨痕影子在缓慢移动。",
    "camera_motion": "Static",
    "ambiance_audio": "雨声、油灯火苗的细微噼啪声、纸张被指腹摩挲的沙沙声",
    "dialogue": []
  },
  "transition_to_next": "cut"
}
```

要点：
- `scene` 描写**此刻这一帧**真实可见的元素，不混入过去 / 未来
- `lighting` 给出具体光源 / 方向 / 色温，不写"光影神秘"这类抽象词
- `ambiance_audio` 只写画内音（diegetic sound），不写 BGM / 旁白
- `action` 单一连贯动作，避免蒙太奇 / 跨时空切换

## 输出格式

生成的 JSON 文件保存至 `scripts/episode_N.json`，核心结构：

- `episode`、`content_mode`、`novel`（title、chapter、source_file）
- narration 模式：`segments[]`（每个片段含 visual、novel_text、duration_seconds 等）
- drama 模式：`scenes[]`（每个场景含 visual、dialogue、action、duration_seconds 等）
- reference_video 模式：`video_units[]`（每个 unit 含 `shots[]`、`references[]`、`duration_seconds` 等），`metadata.total_units`
- `metadata`：total_segments / total_scenes、created_at、generator
- `duration_seconds`：全集总时长（秒）

## `--dry-run` 输出

打印将发送给文本模型的完整 prompt 文本，不调用 API、不写文件。用于检查 prompt 质量和长度。

> 三种生成模式的数据路径、预处理 subagent、schema 选择详见 `.claude/references/generation-modes.md`。
