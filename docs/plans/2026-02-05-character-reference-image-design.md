# 角色参考图功能设计

**日期**: 2026-02-05  
**状态**: 已实现

## 概述

为角色生成功能添加参考图支持。用户可以上传一张参考图（如演员照片、手绘草稿），生成角色设计图时 AI 会参考该图片保持角色外貌一致性。

## 术语定义

| 类型 | 用途 | 来源 |
|------|------|------|
| **参考图 (reference_image)** | 生成角色设计图时作为 AI 输入，控制外貌 | 用户上传 |
| **设计图 (character_sheet)** | 生成分镜图/视频时作为参考，确保角色一致 | AI 生成 |

## 数据结构变更

### project.json

`characters` 结构新增 `reference_image` 字段：

```json
{
  "characters": {
    "姜月茴": {
      "description": "二十出头女子，鹅蛋脸，柳叶眉...",
      "reference_image": "characters/refs/姜月茴.png",
      "character_sheet": "characters/姜月茴.png",
      "voice_style": "温柔但有威严"
    }
  }
}
```

### 文件存储结构

```
projects/{项目名}/
├── characters/
│   ├── refs/           # 新增：参考图目录
│   │   └── 姜月茴.png  # 用户上传的参考图
│   └── 姜月茴.png      # AI 生成的设计图
```

## 后端 API 变更

### 1. 文件上传路由 (`webui/server/routers/files.py`)

新增上传类型 `character_ref`：

- 路径：`POST /projects/{project_name}/upload/character_ref?name={char_name}`
- 保存到：`characters/refs/{name}.png`
- 自动更新 `project.json` 中的 `reference_image` 字段

在 `ALLOWED_EXTENSIONS` 中添加：
```python
"character_ref": [".png", ".jpg", ".jpeg", ".webp"],
```

在 `upload_file` 函数中添加处理逻辑。

### 2. 角色管理路由 (`webui/server/routers/characters.py`)

`UpdateCharacterRequest` 新增可选字段：
```python
reference_image: Optional[str] = None
```

更新逻辑中处理该字段。

### 3. 生成路由 (`webui/server/routers/generate.py`)

`generate_character` 端点增加逻辑：
1. 检查角色是否有 `reference_image` 字段
2. 若有，读取图片文件
3. 传入 `MediaGenerator.generate_image_async()` 的 `reference_images` 参数

### 4. CLI 脚本 (`.claude/skills/generate-characters/scripts/generate_character.py`)

- **移除** `--ref` 命令行参数
- 自动从 `project.json` 读取角色的 `reference_image` 字段
- 若存在则加载图片作为参考

## 前端 WebUI 变更

### 角色编辑弹窗布局

参考图和设计图上下排布：

```
┌─────────────────────────────────────────────────┐
│  编辑角色                                    [X] │
├─────────────────────────────────────────────────┤
│  名称：[姜月茴_____________]                    │
│  描述：[二十出头女子...____]                    │
│  声线：[温柔但有威严_______]                    │
├─────────────────────────────────────────────────┤
│  参考图（用户上传）                             │
│  ┌──────────────────────────────────────┐      │
│  │                                      │      │
│  │          [预览图/占位符]             │      │
│  │                                      │      │
│  └──────────────────────────────────────┘      │
│  [选择文件...]                                  │
├─────────────────────────────────────────────────┤
│  设计图（AI 生成）                              │
│  ┌──────────────────────────────────────┐      │
│  │                                      │      │
│  │          [预览图/占位符]             │      │
│  │                                      │      │
│  └──────────────────────────────────────┘      │
│  [生成设计图]  版本: [v1 ▼] [还原]             │
├─────────────────────────────────────────────────┤
│                    [保存] [取消]                │
└─────────────────────────────────────────────────┘
```

### 交互逻辑

1. **选择参考图**：选择文件后暂存在前端（File 对象），显示预览
2. **点击保存**：
   - 若有新选择的参考图文件 → 先调用 `upload/character_ref` API
   - 再保存角色数据（包含 `reference_image` 路径）
3. **生成设计图**：调用 `generate/character` API，后端自动读取参考图
4. **版本控制**：设计图支持版本管理（现有功能）

### 涉及文件

- `webui/js/project/characters.js` - 编辑弹窗逻辑
- `webui/index.html` - 弹窗 HTML 结构（若需要）

## 用户操作流程

```
1. 添加/编辑角色 → 填写名称、描述
         ↓
2. 选择参考图（可选）→ 前端预览
         ↓
3. 点击"保存" → 上传参考图 + 保存角色数据
         ↓
4. 点击"生成设计图" → API 自动使用参考图 → AI 生成
         ↓
5. 查看设计图 → 不满意可重新生成（版本管理）
```

## 关键设计决定

| 项目 | 决定 | 理由 |
|------|------|------|
| 新增字段名 | `reference_image` | 与 `character_sheet` 对应，语义清晰 |
| 存储路径 | `characters/refs/{name}.png` | 单独目录，文件组织清晰 |
| 参考图数量 | 单张 | 简化实现，满足主要场景 |
| UI 布局 | 上下排布 | 符合用户阅读习惯 |
| 保存时机 | 点击保存时一并上传 | 避免临时文件残留 |
| CLI --ref 参数 | 移除 | 统一从 project.json 读取，减少用户操作 |

## 实现清单

### 后端

- [ ] `files.py`: 添加 `character_ref` 上传类型
- [ ] `characters.py`: `UpdateCharacterRequest` 添加 `reference_image` 字段
- [ ] `generate.py`: `generate_character` 读取并使用参考图
- [ ] `generate_character.py` (CLI): 移除 `--ref` 参数，自动从 project.json 读取

### 前端

- [ ] `characters.js`: 弹窗添加参考图上传区域
- [ ] `characters.js`: 保存时处理参考图上传
- [ ] `index.html`: 更新弹窗 HTML 结构（若需要）
