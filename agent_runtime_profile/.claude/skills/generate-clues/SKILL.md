---
name: generate-clues
description: 生成线索设计参考图（道具/环境）。当用户说"生成线索图"、"画道具设计"、想为重要物品或场景创建参考图、或有 major 线索缺少 clue_sheet 时使用。确保跨场景视觉一致。
---

# 生成线索设计图

使用 Gemini 3 Pro Image API 创建线索设计图，确保整个视频中重要物品和环境的视觉一致性。

> Prompt 编写原则详见 `references/content-modes.md` 的"Prompt 语言"章节。

## 线索类型

- **道具类（prop）**：信物、武器、信件、首饰等关键物品
- **环境类（location）**：标志性建筑、特定树木、重要场所等

## 线索描述编写指南

编写 `description` 时使用**叙事式写法**，不要罗列关键词。

**道具示例**：
> "一块翠绿色的祖传玉佩，约拇指大小，玉质温润透亮。表面雕刻着精致的莲花纹样，花瓣层层舒展。玉佩上系着一根红色丝绳，打着传统的中国结。"

**环境示例**：
> "村口的百年老槐树，树干粗壮需三人合抱，树皮龟裂沧桑。主干上有一道明显的雷击焦痕，从顶部蜿蜒而下。树冠茂密，夏日里洒下斑驳的树影。"

**要点**：用连贯段落描述形态、质感、细节，突出能跨场景识别的独特特征。

## 命令行用法

```bash
# 生成所有待处理的线索
cd projects/{project_name} && python ../../.claude/skills/generate-clues/scripts/generate_clue.py --all

# 生成指定单个线索
cd projects/{project_name} && python ../../.claude/skills/generate-clues/scripts/generate_clue.py --clue "玉佩"

# 生成指定多个线索
cd projects/{project_name} && python ../../.claude/skills/generate-clues/scripts/generate_clue.py --clues "玉佩" "老槐树" "密信"

# 列出待生成的线索
cd projects/{project_name} && python ../../.claude/skills/generate-clues/scripts/generate_clue.py --list
```

## 工作流程

1. **加载项目元数据** — 从 project.json 找出 `importance='major'` 且缺少 `clue_sheet` 的线索
2. **生成线索设计** — 根据类型（prop/location）选择对应模板，调用脚本生成
3. **审核检查点** — 展示每张设计图，用户可批准或要求重新生成
4. **更新 project.json** — 更新 `clue_sheet` 路径

## Prompt 模板

### 道具类（prop）
```
一张专业的道具设计参考图，{项目 style}。

道具「[名称]」的多视角展示。[详细描述 - 叙事式段落]

三个视图水平排列在纯净浅灰背景上：左侧正面全视图、中间45度侧视图展示立体感、右侧关键细节特写。柔和均匀的摄影棚照明，高清质感，色彩准确。
```

### 环境类（location）
```
一张专业的场景设计参考图，{项目 style}。

标志性场景「[名称]」的视觉参考。[详细描述 - 叙事式段落]

主画面占据四分之三区域展示环境整体外观与氛围，右下角小图为细节特写。柔和自然光线。
```

## 质量检查

- 道具：三个视角清晰一致、细节符合描述、特殊纹理清晰可见
- 环境：整体构图和标志性特征突出、光线氛围合适、细节图清晰
