# Agent Prompt 优化设计

**状态**：草案
**日期**：2026-05-07
**分支**：`feat/agent-prompt-optimization`
**参考资料**：
- `docs/小云雀短剧分镜生成SKILLV2.1.md`
- `docs/一，漫剧Seedance提示词指令大全2026.3.27更新.xlsx`

## 1. 背景与目标

ArcReel 现有 prompt 体系（`lib/prompt_builders_script.py` 的 `build_drama_prompt` / `build_narration_prompt`、step1 拆分 subagent、`generate_asset.py` 资产生图）相比小云雀 V2.1 与 Seedance Excel 的爆款 prompt 模板存在以下系统性缺口：

| 维度 | 小云雀 / Seedance | ArcReel 现状 | gap |
|---|---|---|---|
| 爆款节奏铁则 | 强制开篇钩子 / 15 秒冲突节点 / 结尾留坑 | step1 仅按朗读节奏拆分，无爆款规则 | 空白 |
| 防崩指令 | 正向（五官对称、五指完整）+ 负面（畸形/断指/乱码）双控 | 只有 negative_prompt 排 BGM；资产 prompt 无防崩 | 空白 |
| 分镜动态描述 | 微表情 / 发丝衣摆物理飘动 / 环境互动 / 内容融合 | image_prompt / video_prompt 字段说明仅限"具体动作"，无动态优先要求 | 静态、易模板化 |
| 分集结尾留坑 | 单集末镜定格卡点 | 无指引，Gemini 平铺收尾 | 空白 |
| 资产生图模板 | 三视图 / 主图+细节 / 多视角布局 | SKILL.md 写了模板但代码未消费——`generate_asset.py` 直接把 `description` 当作完整 prompt 提交 | 模板未落地 |

**目标**：在**不动 schema**的前提下，把上述能力补齐，让 ArcReel 输出的 `image_prompt.scene` / `video_prompt.action` 文本和资产生图 prompt 直接对齐爆款博主水平。

**非目标**：
- 不动 `NarrationSegment` / `DramaScene` / `DramaEpisodeScript` Pydantic schema
- 不改 generate-storyboard / generate-video 下游模板（下游零改动自动受益于上游更优质）
- 不做 backend 专属 prompt rewriter（视频后端定向适配留二期）
- 不做题材分支爆款配方（玄幻/重生/末世各一套留三期）

## 2. 范围

通过 brainstorming 已经收敛到的 scope：

1. **优化范围** = prompt 内容质量
2. **痛点** = 分集节奏与钩子 + script 阶段生成 prompt 缺指导
3. **改造层级** = 只改 prompt builder + step1 subagent 指令（不动 schema）
4. **content_mode** = drama 优先，narration 同步通用项
5. **资产环节** = analyze-assets + generate-assets 一并升级
6. **落地路径** = B：规则模块化 + 分层注入

## 3. 架构

### 3.1 模块结构

新建 `lib/prompt_rules/` 作为规则单一真相源：

```
lib/prompt_rules/
├── __init__.py            # is_v2_enabled() 灰度开关
├── episode_pacing.py      # 分集节奏铁则（首镜 4 秒 / 15 秒冲突 / 末镜定格卡点）
├── visual_dynamic.py      # 动态视觉规则（微表情 / 物理飘动 / 环境互动 / 内容融合）
├── asset_anti_break.py    # 资产防崩（正向短语 + 负向关键词）
└── asset_layout.py        # 资产布局（角色三视图 / 场景主+细节 / 道具多视角）
```

### 3.2 接入点

| 规则模块 | 接入位置 | 改动方式 |
|---|---|---|
| episode_pacing | `agent_runtime_profile/.claude/agents/normalize-drama-script.md` + `split-narration-segments.md` | 在拆分指引里加节奏铁则段落，文本与 Python 常量逐字一致 |
| episode_pacing | `lib/prompt_builders_script.py:build_drama_prompt` / `build_narration_prompt` 顶部 | `render_pacing_section(content_mode)` 注入 |
| visual_dynamic | `build_drama_prompt` / `build_narration_prompt` 的 `image_prompt.scene` / `video_prompt.action` 字段说明末尾 | append `IMAGE_DYNAMIC_PATCH` / `VIDEO_DYNAMIC_PATCH` |
| asset_anti_break | `agent_runtime_profile/.claude/skills/generate-assets/scripts/generate_asset.py` 的 `_build_specs` / `generate_single` | description 末尾 append 正向防崩；payload 增加 `negative_prompt` |
| asset_layout | 同上 | description 末尾按 type 套布局描述 |

### 3.3 灰度开关

`lib/prompt_rules/__init__.py` 暴露：

```python
def is_v2_enabled() -> bool:
    return os.environ.get("ARCREEL_PROMPT_RULES_V2", "on").lower() != "off"
```

Python 端所有接入点拼接前判断；关闭即退回旧文本。subagent .md 是静态文件不受开关控制（agent runtime 不读环境变量），漂移防御靠测试约束。

## 4. 组件清单

### 4.1 `lib/prompt_rules/episode_pacing.py`

```python
DRAMA_PACING_RULES = """
分集节奏铁则（请把以下要求体现到首镜与末镜的视觉描述上）：
- 开篇钩子：第 1 个分镜的 duration_seconds 设为 4 秒；该镜头画面必须以强视觉冲击/悬念/危机/极致反差作为焦点，杜绝静止介绍性远景。
- 中段冲突密度：每 15 秒至少出现 1 个冲突节点（动作转折 / 情绪反差 / 关系撕裂 / 异常事件），通过分镜的画面权重和镜头景别变化体现。
- 末镜定格卡点：本集最后一个分镜画面停在悬念升级或情绪极致瞬间，shot_type 推荐 Close-up 或 Extreme Close-up，禁止平稳收尾。
"""

NARRATION_PACING_RULES = """
说书节奏要求：
- 首段画面对应朗读前 4 秒，必须用强视觉冲击 / 悬念 / 危机匹配钩子台词，杜绝平铺叙述。
- 末段画面服务于卡点留悬（特写人物 / 关键物件 / 极端表情），shot_type 推荐 Close-up 或 Extreme Close-up。
"""

def render_pacing_section(content_mode: str) -> str:
    if content_mode == "drama":
        return DRAMA_PACING_RULES
    if content_mode == "narration":
        return NARRATION_PACING_RULES
    raise ValueError(f"unknown content_mode: {content_mode}")
```

> "4 秒"硬编码：用户在 brainstorming 中明确选择不做动态注入。简洁优先，未来视频模型最小时长变更时再人工调整。

### 4.2 `lib/prompt_rules/visual_dynamic.py`

```python
IMAGE_DYNAMIC_PATCH = """
- 在描述静态画面时也必须暗示动态：发丝 / 衣摆 / 雨滴 / 落叶 / 尘埃 / 光斑等物理飘动元素至少出现一项。
- 必须包含可观察的微表情：眼神方向、瞳孔聚散、嘴角细微弧度、呼吸状态。
- 环境必须是活的：光影流转 / 雾气浮动 / 热浪扭曲 / 烛火摇曳，至少融入一项。
- 内容融合：禁止使用「画面基调:」「光影设定:」等标题式段落，所有元素融为一段连贯叙述。
- 单字段长度：scene 控制在 200 字以内。
"""

VIDEO_DYNAMIC_PATCH = """
- 动作描述必须包含三层之一：肢体位移（角色在空间中的移动方向）/ 微表情转换（情绪从 A 到 B 的过渡）/ 物理环境互动（角色动作触发的环境反应：脚步扬尘 / 衣摆扫过桌面 / 推门带起气流）。
- 拒绝静态描写。即使是对话场景，也要描写说话人的呼吸节奏、手指小动作或视线偏移。
- 内容融合：把光影变化、氛围演变直接写进动作描述，而不是用独立标题。
- 单字段长度：action 控制在 150 字以内。
"""
```

### 4.3 `lib/prompt_rules/asset_anti_break.py`

```python
CHARACTER_POSITIVE = "人物五官对称、身体结构正常、手指完整为五指、肢体比例协调、面部特征清晰、服装造型完整无穿帮。"
SCENE_POSITIVE     = "场景结构完整、空间透视正常、陈设固定、光影统一、无元素错位。"
PROP_POSITIVE      = "道具结构完整、外观特征清晰、无变形扭曲、焦点明确。"

NEGATIVE_BASE = "畸形, 多肢体, 多指, 断指, 五官扭曲, 面部崩坏, 乱码文字, 水印, 模糊, 低分辨率, 穿帮元素, 严重色差"

def positive_for(asset_type: str) -> str: ...
def negative_for(asset_type: str) -> str: ...
```

`negative_for` 在所有 type 上返回同一份 `NEGATIVE_BASE`，预留按 type 差异化的扩展位（暂时不分化）。

### 4.4 `lib/prompt_rules/asset_layout.py`

```python
CHARACTER_LAYOUT = "三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面。柔和均匀的摄影棚照明，无强烈阴影。"
SCENE_LAYOUT     = "主画面占据四分之三区域展示环境整体外观与氛围，右下角小图为关键细节特写。柔和自然光线。"
PROP_LAYOUT      = "三个视图水平排列在纯净浅灰背景上：正面全视图、45 度侧视图展示立体感、关键细节特写。柔和均匀的摄影棚照明，色彩准确。"

def layout_for(asset_type: str) -> str: ...
```

### 4.5 `generate_asset.py` 包装

在 `_build_specs` 与 `generate_single` 调 `enqueue_and_wait` 之前：

```python
from lib.prompt_rules import is_v2_enabled
from lib.prompt_rules.asset_layout import layout_for
from lib.prompt_rules.asset_anti_break import positive_for, negative_for

def _wrap_prompt(asset_type: str, description: str) -> tuple[str, str | None]:
    if not is_v2_enabled():
        return description, None
    wrapped = f"{description}\n\n{layout_for(asset_type)}\n\n{positive_for(asset_type)}"
    return wrapped, negative_for(asset_type)

# in _build_specs:
prompt, neg = _wrap_prompt(asset_type, assets_dict[name]["description"])
payload = {"prompt": prompt}
if neg is not None:
    payload["negative_prompt"] = neg
```

> 风格前缀（`project.style` / `style_description`）不在本期接入：现有 `description` 已经经由 analyze-assets 写好叙事式段落，叠加风格前缀容易重复污染。如生成质量仍不达标，二期再加。

### 4.6 `prompt_builders_script.py` 接入

```python
# build_drama_prompt 顶部
prompt = f"""你的任务是为剧集动画生成分镜剧本。请仔细遵循以下指示：

{episode_pacing.render_pacing_section('drama') if is_v2_enabled() else ''}

**重要：所有输出内容必须使用{target_language}。仅 JSON 键名和枚举值使用英文。**
...
```

`image_prompt.scene` 字段说明末尾：

```python
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节...
     {visual_dynamic.IMAGE_DYNAMIC_PATCH if is_v2_enabled() else ''}
```

`video_prompt.action` 同样追加 `VIDEO_DYNAMIC_PATCH`。

> 现有约束「每个片段仅选择一种镜头运动」（`camera_motion` 字段说明）保留不动——这是 ArcReel 为视频生成稳定性的主动保守，与"动态优先"指向画面 / 动作内容不冲突。

### 4.7 subagent .md 同步

`agent_runtime_profile/.claude/agents/normalize-drama-script.md` 在「## 工作流程」前补节：

```markdown
## 分集节奏铁则

<逐字粘贴 episode_pacing.DRAMA_PACING_RULES 全文>

拆分剧本时必须遵循上述铁则。
```

`split-narration-segments.md` 同步贴 `NARRATION_PACING_RULES`。

## 5. 端到端数据流

```
Stage A: step1 预处理
  user → /manga-workflow → dispatch normalize-drama-script
    subagent 读 .md（含 ★ DRAMA_PACING_RULES）
    → 调 normalize_drama_script.py（Gemini 3 Pro 拆分镜表）
    → 输出 drafts/episode_N/step1_normalized_script.md
       ─ 首镜 duration_seconds = 4
       ─ 末镜内容描述带定格语义
       ─ 中段每 ~15s 出现冲突节点

Stage B: JSON 剧本生成
  dispatch create-episode-script → generate_script.py
    ScriptGenerator.generate() 调 build_drama_prompt(...)
    prompt 顶部 ← ★ DRAMA_PACING_RULES
    image_prompt.scene 字段说明末尾 ← ★ IMAGE_DYNAMIC_PATCH
    video_prompt.action 字段说明末尾 ← ★ VIDEO_DYNAMIC_PATCH
    → Gemini 按 schema 输出 scripts/episode_N.json

Stage C: 资产生成
  dispatch generate-assets → generate_asset.py
    读 project.json → description
    ★ wrap：description + layout_for(type) + positive_for(type)
    ★ payload["negative_prompt"] = negative_for(type)
    → enqueue_and_wait → image backend

Stage D: 下游不变
  generate-storyboard / generate-video 直接消费 image_prompt / video_prompt
```

## 6. 错误处理与回滚

### 6.1 灰度开关
`ARCREEL_PROMPT_RULES_V2=off` 重启 server → Python 端全部退回旧文本。subagent .md 回滚靠 git revert。

### 6.2 negative_prompt 通道未知
实现第一步必须 grep `lib/image_backends/` 各 provider，分类支持 / 不支持 / 部分支持，记录在实现 plan 里。`generate_asset.py` 始终下发 `negative_prompt`，由各 backend 自行决定是否消费——避免调用方分叉判断。如某 backend 不支持，先在 spec 里标记为已知差异，待二期补上。

### 6.3 subagent .md 与 Python 常量漂移
靠 `tests/prompt_rules/test_subagent_md_sync.py` 用首尾 30 字锚点做 substring 断言；漂移 → CI 红。

### 6.4 Gemini 输出退化
- prompt 文案用"至少出现一项"而非"全部包含"，给 Gemini 选择空间。
- 实施 PR 提交前用 sandbox 项目 `--dry-run` 比对新旧 prompt，再用真 API 抽查 5–10 个分镜判断是否堆砌。
- 关键字段长度上限（scene ≤200、action ≤150）写在 prompt 里防止失控膨胀。

### 6.5 token 上限
观察 `result.usage.output_tokens` 是否接近 `SCRIPT_MAX_OUTPUT_TOKENS = 32000`，必要时调高。

### 6.6 一键回滚预案
1. `ARCREEL_PROMPT_RULES_V2=off` 重启
2. `git revert` agent_runtime_profile 改动并重新部署
3. 已生成的 `scripts/episode_N.json` 不会自动回退；删除后重跑 `/manga-workflow`

## 7. 测试

### 7.1 单元测试

`tests/prompt_rules/test_episode_pacing.py`
- `render_pacing_section("drama")` 含「4 秒」「定格卡点」「15 秒」
- `render_pacing_section("narration")` 含「钩子」「卡点留悬」
- 未知 content_mode → ValueError

`tests/prompt_rules/test_visual_dynamic.py`
- `IMAGE_DYNAMIC_PATCH` 含「微表情」「物理飘动」「内容融合」
- `VIDEO_DYNAMIC_PATCH` 含「肢体位移」「环境反应」「禁止静态描写」

`tests/prompt_rules/test_asset_anti_break.py`
- `positive_for(type)` 互不相同且非空
- `negative_for(...)` 含「畸形」「断指」「乱码」
- 未知 type → ValueError

`tests/prompt_rules/test_asset_layout.py`
- `layout_for("character")` 含「三视图」「正面」「侧面」
- `layout_for("scene")` 含「主画面」「细节」
- `layout_for("prop")` 含「正面」「45 度」「细节」

`tests/prompt_rules/test_v2_switch.py`
- 默认 `is_v2_enabled() == True`
- `ARCREEL_PROMPT_RULES_V2=off` → False，大小写不敏感

### 7.2 同步校验测试

`tests/prompt_rules/test_subagent_md_sync.py`
- 读 `normalize-drama-script.md`，断言含 `DRAMA_PACING_RULES` 首尾 30 字锚点
- 读 `split-narration-segments.md`，断言含 `NARRATION_PACING_RULES` 首尾 30 字锚点

### 7.3 Builder 集成测试

扩展或新建 `tests/test_prompt_builders_script.py`：

- `build_drama_prompt(...)` 输出含 `DRAMA_PACING_RULES` 全文
- `build_drama_prompt(...)` 输出含 `IMAGE_DYNAMIC_PATCH` 与 `VIDEO_DYNAMIC_PATCH`
- 关闭 v2 开关后输出**不**含上述补丁文本
- 输出长度 < 旧版 + 3000 字符

### 7.4 generate_asset 集成测试

`tests/test_generate_asset_prompt_wrap.py`
- fake project（character description = "测试角色"）
- 调 `_build_specs(asset_type="character")`
- 断言 `payload["prompt"]` 末尾依次出现 layout 文本和 positive 防崩短语
- 断言 `payload["negative_prompt"]` 等于 `negative_for("character")`
- 关闭 v2 开关后退回纯 description

### 7.5 端到端 dry-run 比对（验收，非 CI）

```bash
ARCREEL_PROMPT_RULES_V2=off uv run python .../generate_script.py --episode 1 --dry-run > /tmp/prompt_old.txt
ARCREEL_PROMPT_RULES_V2=on  uv run python .../generate_script.py --episode 1 --dry-run > /tmp/prompt_new.txt
diff /tmp/prompt_old.txt /tmp/prompt_new.txt
```

人工抽查 3 个分镜：
- 首镜 `duration_seconds == 4` 且 `image_prompt.scene` 含强冲击元素
- 末镜 shot_type 为 Close-up / Extreme Close-up
- 中段任一分镜 `image_prompt.scene` 至少含微表情 / 物理飘动 / 环境互动其一

### 7.6 不做的测试

- 不做 LLM 输出"是否更爆款"的自动断言
- 不做 image_backends 真生图回归
- 不做旧 builder 路径回归（旧逻辑未动）

## 8. 实施顺序建议

供后续 writing-plans 参考的粗粒度阶段，不在本 spec 强制：

1. 建 `lib/prompt_rules/` 模块 + 单元测试
2. 改 `prompt_builders_script.py` + 集成测试
3. 改 `generate_asset.py` + 集成测试
4. 同步 subagent .md + 同步校验测试
5. 探测 image_backends 的 negative_prompt 兼容性，必要时补 backend 适配
6. 端到端 dry-run + 真 API 抽查
7. 灰度开关默认开，观察一段时间后清理灰度代码（spec 范围外）

## 9. 已接受的代价

- **schema 不变**：末镜定格卡点完全靠 Gemini 自觉体现，无结构化字段。如 Gemini 表现不稳定，二期补 `cliffhanger: bool`。
- **subagent .md 与 Python 常量必须手工保持一致**：靠测试卡漂移，不是零成本。
- **不做题材分支**：玄幻 / 重生 / 末世共用同一套节奏铁则，效果上限受限。
- **不做 backend 专属适配**：seedance / 即梦 / veo 共用同一组动态规则，未做 backend 风味化。
