---
name: generate-assets
description: "统一资产生成 skill：接受 `--type=character|scene|prop`，或不传自动扫所有 pending（缺 sheet）资源并按类型分发。当用户说"生成角色图"/"生成场景图"/"生成道具图"、想为新资产创建参考图、或有资产缺少 *_sheet 时使用。"
---

# 生成资产设计图

为项目的角色、场景、道具创建参考设计图，保证整个视频中视觉元素的一致性。
图像供应商由项目设置选择（不锁定具体 backend）。

> Prompt 编写原则详见 `.claude/references/generation-modes.md` 的"Prompt 语言"章节。

## 共同约定

- 所有资产 `description` 用**叙事式段落**，而不是关键词列表。
- 用户只需在 project.json 中维护 `description`；最终交给图像 backend 的完整 prompt
  （含布局 / 防崩短语 / 反向提示词）由 `lib/prompt_builders.py` 在 server 端拼好，
  WebUI 与 Skill 走同一份真相源。
- Pending 判定：对应资产的 `*_sheet` 字段为空或文件不存在。

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

入队走 SDK in-process MCP tool（在主进程跑，不受 sandbox 网络/文件约束）：

| 操作 | 工具 |
|------|------|
| 列出所有/某类 pending | `mcp__arcreel__list_pending_assets({"type": "character"})`（type 可省略） |
| 生成所有 pending（三类各一轮） | `mcp__arcreel__generate_assets({})` |
| 生成某类全部 pending | `mcp__arcreel__generate_assets({"type": "character"})` |
| 生成指定多个 | `mcp__arcreel__generate_assets({"type": "prop", "names": ["玉佩", "密信"]})` |
| 生成单个 | `mcp__arcreel__generate_assets({"type": "scene", "names": ["村口老槐树"]})` |

返回 `is_error: true` 时，文本里包含失败明细，按需重试或反馈给开发者。

### 旧 CLI → 新 tool 对照

| 旧脚本（已删除） | 新 tool |
|---|---|
| `python ... generate_asset.py --all` | `mcp__arcreel__generate_assets({})` |
| `python ... generate_asset.py --type T --all` | `mcp__arcreel__generate_assets({"type": "T"})` |
| `python ... generate_asset.py --type T --name N` | `mcp__arcreel__generate_assets({"type": "T", "names": ["N"]})` |
| `python ... generate_asset.py --type T --names ...` | `mcp__arcreel__generate_assets({"type": "T", "names": [...]})` |
| `python ... generate_asset.py --list` | `mcp__arcreel__list_pending_assets({})` |
| `python ... generate_asset.py --type T --list` | `mcp__arcreel__list_pending_assets({"type": "T"})` |

## 工作流程

1. **加载项目元数据** — 从 project.json 找出缺少对应 `*_sheet` 的资产
2. **入队生成任务** — description 直接作为 prompt 提交；server 端 `lib.prompt_builders` 注入布局 / 防崩 / 反向
3. **审核检查点** — 展示每张设计图，用户可批准或要求重新生成
4. **更新 project.json** — 更新 `character_sheet` / `scene_sheet` / `prop_sheet` 路径

## 质量检查

- **角色**：四个面板（特写 + 三视图）的面部、发型、服装、配饰完全一致
- **场景**：整体构图和标志性特征突出、光线氛围合适、细节图清晰
- **道具**：三个视角清晰一致、细节符合描述、特殊纹理清晰可见
