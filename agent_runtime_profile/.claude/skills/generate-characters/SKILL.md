---
name: generate-characters
description: 生成人物设计参考图（三视图）。当用户说"生成人物图"、"画角色设计"、想为新角色创建参考图、或有角色缺少 character_sheet 时使用。确保视频中人物形象一致。
---

# 生成人物设计图

使用 Gemini 3 Pro Image API 创建人物设计图，确保整个视频中的视觉一致性。

> Prompt 编写原则详见 `references/content-modes.md` 的"Prompt 语言"章节。

## 人物描述编写指南

编写人物 `description` 时使用**叙事式写法**，不要罗列关键词。

**推荐**：
> "二十出头的女子，身材纤细，鹅蛋脸上有一双清澈的杏眼，柳叶眉微蹙时带着几分忧郁。身着淡青色绣花罗裙，腰间系着同色丝带，显得端庄而不失灵动。"

**要点**：用连贯段落描述外貌、服装、气质，包含年龄、体态、面部特征、服饰细节。

## 命令行用法

```bash
# 生成所有待处理的角色
cd projects/{project_name} && python ../../.claude/skills/generate-characters/scripts/generate_character.py --all

# 生成指定单个人物
cd projects/{project_name} && python ../../.claude/skills/generate-characters/scripts/generate_character.py --character "{角色名}"

# 生成指定多个人物
cd projects/{project_name} && python ../../.claude/skills/generate-characters/scripts/generate_character.py --characters "{角色1}" "{角色2}" "{角色3}"

# 列出待生成的角色
cd projects/{project_name} && python ../../.claude/skills/generate-characters/scripts/generate_character.py --list
```

## 工作流程

1. **加载项目数据** — 从 project.json 找出缺少 `character_sheet` 的人物
2. **生成人物设计** — 根据描述构建 prompt，调用脚本生成
3. **审核检查点** — 展示每张设计图，用户可批准或要求重新生成
4. **更新 project.json** — 更新 `character_sheet` 路径

## Prompt 模板

```
一张专业的人物设计参考图，{项目 style}。

人物「[人物名称]」的三视图设计稿。[人物描述 - 叙事式段落]

三个等比例全身像水平排列在纯净浅灰背景上：左侧正面、中间四分之三侧面、右侧纯侧面轮廓。柔和均匀的摄影棚照明，无强烈阴影。
```

> 画风由项目的 `style` 字段决定，不使用固定的"漫画/动漫"描述。
