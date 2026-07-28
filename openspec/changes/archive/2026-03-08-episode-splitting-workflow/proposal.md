## Why

当前体系缺少**小说 → 集数的映射机制**。用户上传完整小说后，系统直接将全文传给 Gemini 生成剧本，但无法指定"这一集只用小说的某一部分"。具体表现：

1. `normalize_drama_script.py` 默认读取 `source/` 下**所有文件拼接**，`--source` 参数只能指定整个文件，不能指定文件内的范围
2. `split-narration-segments` subagent 的描述中提到"本集小说文本范围"，但没有实际机制让用户定义或切分这个范围
3. `manga-workflow` 编排 dispatch 时写了 `本集小说范围：{章节名/文件名/起止说明}`，但这个值无从获取——主 agent 不知道小说哪部分对应哪一集

后果：
- 10 万字小说全量灌入 Gemini，生成质量不可控（模型自行决定用哪部分）
- 用户无法按需制作某一集（比如只想先做前 1000 字的内容）
- 多集制作时缺少一致的分集边界

## What Changes

新增**渐进式分集规划**机制：通过两个脚本实现人机协作的分集流程。

### 核心思路

```
用户指定目标字数（如 1000 字/集）
    ↓
peek 脚本展示切分点附近上下文（前后各 200 字）
    ↓
Agent 阅读上下文，建议自然断点（句号、段落、章节边界）
    ↓
用户确认或调整
    ↓
split --anchor "断点前文本" --dry-run  验证切分位置
    ↓
确认无误 → split 实际执行：episode_N.txt + _remaining.txt
    ↓
循环处理下一集
```

### 新增脚本

**1. `peek_split_point.py`** — 切分点探测

```bash
python peek_split_point.py --source source/novel.txt --target 1000 --context 200
```

- 输入：源文件路径、目标字数、上下文字数（默认 200）
- 计数规则：含标点，不含空行（纯格式性空白行）
- 输出：切分点前后的上下文文本 + 元信息（总字数、目标位置、实际字符偏移）

**2. `split_episode.py`** — 执行切分

```bash
# 先 dry run 验证切分位置
python split_episode.py --source source/novel.txt --episode 1 --anchor "他转身离开了。" --dry-run

# 确认无误后实际执行
python split_episode.py --source source/novel.txt --episode 1 --anchor "他转身离开了。"
```

- 输入：源文件路径、集数、锚点文本（切分点前的 10-20 个字符）
- `--dry-run`：仅展示切分预览（前文末尾 + 后文开头各 50 字），不写文件
- 锚点匹配到多处时报错，要求提供更长的锚点文本
- 输出：
  - `source/episode_N.txt` — 本集内容
  - `source/_remaining.txt` — 剩余内容（覆盖式更新）
- 原文件始终保留不动

### 工作流集成

分集规划作为**阶段 2（单集预处理）的前置检查**嵌入 `manga-workflow`：

```
阶段 2 触发时：
  检查 source/episode_{N}.txt 是否存在
    ├─ 存在 → 直接进入预处理
    └─ 不存在 → 触发分集规划流程：
         1. 主 agent 询问用户目标字数（或使用上次设定）
         2. dispatch subagent 调用 peek_split_point.py
         3. Agent 分析上下文，建议切分点
         4. 用户确认
         5. 调用 split_episode.py 执行切分
         6. 继续进入预处理（使用 episode_N.txt）
```

### 按需单集切分

分集规划**不是一次性把整部小说拆完**，而是每次只切分当前需要制作的那一集：

```
制作第 1 集时:
  source/episode_1.txt 不存在
  → peek novel.txt → 确认 → split → episode_1.txt + _remaining.txt
  → 继续制作第 1 集的预处理、剧本生成、资产生成...

（过了几天）制作第 2 集时:
  source/episode_2.txt 不存在
  → peek _remaining.txt → 确认 → split → episode_2.txt + _remaining.txt（更新）
  → 继续制作第 2 集...

用户可以随时停下，不必一口气规划所有集数。
```

### 现有脚本适配

`normalize_drama_script.py` 和 `split-narration-segments` subagent 不需要大改——只需在 dispatch 时指定 `--source source/episode_N.txt`，让它们读取已切分好的单集文件而非全量小说。

## Capabilities

### New Capabilities
- `episode-splitting`: 渐进式分集规划——peek 探测切分点 + 人机协作确认 + 物理切分为 per-episode 文件

### Modified Capabilities
- `workflow-orchestration`（来自 refactor-script-creation-workflow）: manga-workflow 阶段 2 增加前置检查，缺少 episode 文件时触发分集流程

## Impact

- **新增文件**：
  - `agent_runtime_profile/.claude/skills/manage-project/scripts/peek_split_point.py`
  - `agent_runtime_profile/.claude/skills/manage-project/scripts/split_episode.py`
- **修改文件**：
  - `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` — 阶段 2 增加前置检查逻辑
  - `agent_runtime_profile/.claude/settings.json` — 添加两个新脚本的 Bash 执行权限
- **不受影响**：
  - `normalize_drama_script.py` — 已支持 `--source` 参数，无需修改
  - `split-narration-segments` subagent — dispatch 时指定文件路径即可
  - 后端服务、前端、数据模型均不受影响
