---
name: generate-assets
description: "统一资产生成 skill：接受 `--type=character|scene|prop`，或不传自动扫所有 pending（缺 sheet）资源并按类型分发。当用户说"生成角色图"/"生成场景图"/"生成道具图"、想为新资产创建参考图、或有资产缺少 *_sheet 时使用。"
---

# 生成资产设计图

使用 Gemini 3 Pro Image API 创建角色、场景、道具设计图，确保整个视频中视觉元素的一致性。

> Prompt 编写原则详见 `.claude/references/content-modes.md` 的"Prompt 语言"章节。

---

## 角色（character）

### 角色描述编写指南

编写角色 `description` 时使用**叙事式写法**，不要罗列关键词。

**推荐**：
> "二十出头的女子，身材纤细，鹅蛋脸上有一双清澈的杏眼，柳叶眉微蹙时带着几分忧郁。身着淡青色绣花罗裙，腰间系着同色丝带，显得端庄而不失灵动。"

**要点**：用连贯段落描述外貌、服装、气质，包含年龄、体态、面部特征、服饰细节。

### Pending 判定

`character_sheet` 字段为空（或文件不存在）的角色即为待处理。

### Prompt 模板（角色）

```
一张专业的角色设计参考图，{项目 style}。

角色「[角色名称]」的三视图设计稿。[角色描述 - 叙事式段落]

三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面轮廓。柔和均匀的摄影棚照明，无强烈阴影。
```

> 画风由项目的 `style` 字段决定，不使用固定的"漫画/动漫"描述。

---

## 场景（scene）

### 场景描述编写指南

编写场景 `description` 时使用**叙事式写法**，描述环境整体外观与氛围。

**示例**：
> "村口的百年老槐树，树干粗壮需三人合抱，树皮龟裂沧桑。主干上有一道明显的雷击焦痕，从顶部蜿蜒而下。树冠茂密，夏日里洒下斑驳的树影。"

**要点**：用连贯段落描述形态、光线、氛围，突出能跨场景识别的独特特征。

### Pending 判定

`scene_sheet` 字段为空（或文件不存在）的场景即为待处理。

### Prompt 模板（场景）

```
一张专业的场景设计参考图，{项目 style}。

标志性场景「[名称]」的视觉参考。[详细描述 - 叙事式段落]

主画面占据四分之三区域展示环境整体外观与氛围，右下角小图为细节特写。柔和自然光线。
```

---

## 道具（prop）

### 道具描述编写指南

编写道具 `description` 时使用**叙事式写法**，不要罗列关键词。

**示例**：
> "一块翠绿色的祖传玉佩，约拇指大小，玉质温润透亮。表面雕刻着精致的莲花纹样，花瓣层层舒展。玉佩上系着一根红色丝绳，打着传统的中国结。"

**要点**：用连贯段落描述形态、质感、细节，突出能跨场景识别的独特特征。

### Pending 判定

`prop_sheet` 字段为空（或文件不存在）的道具即为待处理。

### Prompt 模板（道具）

```
一张专业的道具设计参考图，{项目 style}。

道具「[名称]」的多视角展示。[详细描述 - 叙事式段落]

三个视图水平排列在纯净浅灰背景上：左侧正面全视图、中间45度侧视图展示立体感、右侧关键细节特写。柔和均匀的摄影棚照明，高清质感，色彩准确。
```

---

## 命令行用法

```bash
# 生成所有类型的待处理资产
python .claude/skills/generate-assets/scripts/generate_asset.py --all

# 生成指定类型的所有待处理资产
python .claude/skills/generate-assets/scripts/generate_asset.py --type character --all
python .claude/skills/generate-assets/scripts/generate_asset.py --type scene --all
python .claude/skills/generate-assets/scripts/generate_asset.py --type prop --all

# 生成指定单个资产
python .claude/skills/generate-assets/scripts/generate_asset.py --type character --name "张三"
python .claude/skills/generate-assets/scripts/generate_asset.py --type scene --name "村口老槐树"
python .claude/skills/generate-assets/scripts/generate_asset.py --type prop --name "玉佩"

# 生成指定多个资产
python .claude/skills/generate-assets/scripts/generate_asset.py --type character --names "张三" "李四"
python .claude/skills/generate-assets/scripts/generate_asset.py --type prop --names "玉佩" "密信"

# 列出所有类型的待处理资产
python .claude/skills/generate-assets/scripts/generate_asset.py --list

# 列出指定类型的待处理资产
python .claude/skills/generate-assets/scripts/generate_asset.py --type character --list
python .claude/skills/generate-assets/scripts/generate_asset.py --type scene --list
python .claude/skills/generate-assets/scripts/generate_asset.py --type prop --list
```

## 工作流程

1. **加载项目元数据** — 从 project.json 找出缺少对应 `*_sheet` 的资产
2. **生成资产设计** — 根据类型选择对应模板，调用脚本生成
3. **审核检查点** — 展示每张设计图，用户可批准或要求重新生成
4. **更新 project.json** — 更新 `character_sheet` / `scene_sheet` / `prop_sheet` 路径

## 质量检查

- **角色**：三个视角清晰一致、外貌服装符合描述、整体风格与项目 style 匹配
- **场景**：整体构图和标志性特征突出、光线氛围合适、细节图清晰
- **道具**：三个视角清晰一致、细节符合描述、特殊纹理清晰可见
