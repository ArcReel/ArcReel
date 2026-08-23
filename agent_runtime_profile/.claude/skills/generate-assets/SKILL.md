---
name: generate-assets
description: >-
  统一资产生成 skill：接受 `--type=character|scene|prop`，或不传自动扫所有 pending（缺 sheet）资源并按类型分发。当用户说“生成角色图”/“生成场景图”/“生成道具图”、想为新资产创建参考图、或有资产缺少 *_sheet 时使用。
---

# 生成角色、场景与道具资产

为项目的角色、场景、道具创建资产图，保证整个视频中视觉元素的一致性。
图像供应商由项目设置选择（不锁定具体 backend）。

角色声音是独立的并发资产：`complete_asset_inventory` 提交角色集后，服务端会立即为缺少
有效声音的角色入队参考音频候选，不等待 `character_sheet`。默认以 `description + voice_style`
生成约 10 秒的单人干净独白视频，抽取/裁剪 WAV 后删除视频；已有 `voice_id`、
`reference_audio`、已选全局声音或待确认候选时跳过。候选必须试听确认后才写入
`reference_audio`，TTS 仍作为可选回退。

> Prompt 编写原则详见 `.claude/references/generation-modes.md` 的"Prompt 语言"章节。

## 共同约定

- 所有资产 `description` 用**叙事式段落**，而不是关键词列表。
- 用户只需在 project.json 中维护 `description`；最终交给图像 backend 的完整 prompt
  （含布局 / 防崩短语 / 反向提示词）由 `lib/prompt_builders.py` 在 server 端拼好，
  WebUI 与 Skill 走同一份真相源。
- Pending 判定：Artifact Manifest 中该资产图状态为 `missing`；`stale` 产物复用，不计入待生成。

---

## 角色（character）

### description 编写指南

用连贯段落描述外貌、服装、气质，包含年龄、体态、面部特征、服饰细节。

**示例**：

> "二十出头的女子，身材纤细，鹅蛋脸上有一双清澈的杏眼，柳叶眉微蹙时带着几分忧郁。身着淡青色绣花罗裙，腰间系着同色丝带，显得端庄而不失灵动。"

### 输出布局

横版 16:9 四格设计稿，纯白背景：左侧约 40% 宽度的胸像特写，右侧三个 A-Pose 全身视图（正面 / 四分之三侧面 / 背面）。
所有面板中角色面部、发型、服装、配饰需保持完全一致。

> 用户填写 description 时只需关心外貌 / 服装等内容；布局由 builder 注入。

---

## 场景（scene）

### description 编写指南

用连贯段落描述形态、光线、氛围，突出能跨场景识别的独特特征。

**示例**：

> "村口的百年老槐树，树干粗壮需三人合抱，树皮龟裂沧桑。主干上有一道明显的雷击焦痕，从顶部蜿蜒而下。树冠茂密，夏日里洒下斑驳的树影。"

### 输出布局

主画面占四分之三区域展示环境整体外观与氛围，右下角嵌入关键细节小图。

---

## 道具（prop）

### description 编写指南

用连贯段落描述形态、质感、细节，突出能跨场景识别的独特特征。

**示例**：

> "一块翠绿色的祖传玉佩，约拇指大小，玉质温润透亮。表面雕刻着精致的莲花纹样，花瓣层层舒展。玉佩上系着一根红色丝绳，打着传统的中国结。"

### 输出布局

三视图水平排列于纯净浅灰背景：正面全视图、45° 侧视图、关键细节特写。

---

## 工具调用

入队走 MCP 工具：

| 操作 | 工具 |
|------|------|
| 列出所有/某类 pending | `mcp__arcreel__list_pending_assets({"type": "character"})`（type 可省略） |
| 生成所有 pending（三类各一轮） | `mcp__arcreel__generate_assets({})` |
| 生成某类全部 pending | `mcp__arcreel__generate_assets({"type": "character"})` |
| 生成指定多个 | `mcp__arcreel__generate_assets({"type": "prop", "names": ["玉佩", "密信"]})` |
| 生成单个 | `mcp__arcreel__generate_assets({"type": "scene", "names": ["村口老槐树"]})` |
| 补生成角色声音候选 | `mcp__arcreel__generate_character_voice_references({"names": ["张三"]})` |
| 用 TTS 生成候选 | `mcp__arcreel__generate_character_voice_references({"names": ["张三"], "strategy": "tts", "voice": "VoiceId"})` |
| 用户试听认可后确认 | `mcp__arcreel__confirm_character_voice_reference({"name": "张三", "task_id": "..."})` |

结果按 `requested / succeeded / failed / blocked / skipped` 逐 ID 返回，ID 形如 `character/张三`；
已失效但可复用的旧图进入 `skipped`，不会自动重生；
按每一项自带的 `problem.code` 与 `problem.action` 决定下一步，不要解析文本。
结构详见 `.claude/references/generation-results.md`。

## 工作流程

1. **加载项目元数据** — 从 Artifact Manifest 找出资产图状态为 `missing` 的资产
2. **并发入队** — 资产图按 description 提交；缺声音候选通常已在角色提取完成时独立入队，不得等待角色图完成后才触发
3. **审核检查点** — 展示每张资产图；声音只展示提取后的音频候选供试听，不展示内部独白视频
4. **声音确认** — 只有用户试听认可后才调用 `confirm_character_voice_reference`；未认可可重新生成或切换 TTS
5. **更新 project.json** — 更新 `character_sheet` / `scene_sheet` / `prop_sheet`，确认后的声音写入角色 `reference_audio`

## 审核检查点：编辑 vs 重新生成

用户对资产图提意见时先判断诉求类型，选错路径会推翻已满意的部分或丢掉预期外的改动：

- **只想改局部**（换发色、去掉杂物、调整光线氛围等），且构图和整体设计满意 → 用
  `mcp__arcreel__edit_images({"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]})`
  保底图微调，一次可对同类型多个资产批量下发
- **想推翻构图/整体设计重来**，或本来就要改 description（进而改变后续按 description
  重新生成的结果）→ 用 `generate_assets` 按更新后的 description 重新生成整图
- 编辑不会更新 `description` / prompt——编辑后再触发 `generate_assets` 仍按原 description
  重画，编辑效果只能从版本历史找回

## 质量检查

- **角色**：四个面板（特写 + 三视图）的面部、发型、服装、配饰完全一致
- **场景**：整体构图和标志性特征突出、光线氛围合适、细节图清晰
- **道具**：三个视角清晰一致、细节符合描述、特殊纹理清晰可见
