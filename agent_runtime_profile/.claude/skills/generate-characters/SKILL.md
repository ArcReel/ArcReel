---
name: generate-characters
description: 使用 Gemini 图像生成 API 为视频生成人物设计图。使用场景：(1) 用户需要为项目生成人物参考图，(2) 用户运行 /generate-characters 命令，(3) 剧本中有人物没有 character_sheet 路径。生成一致的人物设计用于分镜和视频生成。
---

# 生成人物设计图

使用 Gemini 3 Pro Image API 创建人物设计图，确保整个视频中的视觉一致性。

## Prompt 最佳实践

在编写人物描述和生成 Prompt 前，请先阅读 `docs/nano-banana.md` 第 365 行起的 **Prompting guide and strategies** 章节，了解 Gemini 图像生成的最佳实践。

**核心原则**：
> "Describe the scene, don't just list keywords. A narrative, descriptive paragraph will almost always produce a better, more coherent image than a list of disconnected words."

## 人物描述编写指南

编写人物 `description` 时，请遵循**叙事式写法**：

✅ **推荐**:
> "二十出头的女子，身材纤细，鹅蛋脸上有一双清澈的杏眼，柳叶眉微蹙时带着几分忧郁。身着淡青色绣花罗裙，腰间系着同色丝带，显得端庄而不失灵动。"

❌ **避免**:
> "20岁，女，杏眼，柳叶眉，青色裙子"

**要点**:
- 用连贯段落描述外貌、服装、气质
- 包含年龄、体态、面部特征、服饰细节
- 可加入人物气质或情绪暗示

## 工作流程

1. **加载项目剧本**
   - 如未指定项目名称，询问用户
   - 从 `projects/{项目名}/scripts/` 加载剧本 JSON
   - 列出没有 `character_sheet` 路径的人物

2. **生成人物设计**
   - 对于每个人物：
     - 根据描述构建详细的 prompt
     - 运行 `.claude/skills/generate-characters/scripts/generate_character.py`
     - 保存到 `projects/{项目名}/characters/`

3. **审核检查点**
   - 展示每张生成的人物图
   - 询问用户是否批准或重新生成
   - 允许调整描述

4. **更新剧本**
   - 更新剧本 JSON 中的 `character_sheet` 路径
   - 保存更新后的剧本

## 人物设计 Prompt 模板

```
一张专业的人物设计参考图，{项目 style}。

人物「[人物名称]」的三视图设计稿。[人物描述 - 叙事式段落]

三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面轮廓。柔和均匀的摄影棚照明，无强烈阴影。
```

> 画风由项目的 `style` 字段决定，不使用固定的"漫画/动漫"描述。

## API 使用

使用 `lib/gemini_client.py`：

```python
from lib.gemini_client import GeminiClient

client = GeminiClient()
image = client.generate_image(
    prompt=character_prompt,
    aspect_ratio="9:16",
    output_path=f"projects/{项目名}/characters/{人物名}.png"
)
```
