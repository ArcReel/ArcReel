# WebUI 图片/视频重新生成与版本控制设计

**日期**: 2026-01-29
**状态**: 待实现

---

## 需求概述

1. 在 WebUI 编辑模态框中添加生成/重新生成按钮，调用 GeminiClient 异步方法生成图片/视频
2. 对分镜图、视频、角色图、线索图引入版本号机制，保留历史版本并可还原
3. 历史版本的图片/视频与对应的 prompt 绑定

---

## 设计决策

| 决策项 | 选择 |
|--------|------|
| 覆盖场景 | 片段/场景分镜图、视频、角色设计图、线索设计图（共 4 类） |
| 生成交互 | 模态框内 loading 状态，不阻断其他编辑 |
| 版本存储 | `versions/` 集中目录，当前版本保持原路径 |
| 版本元数据 | `versions/versions.json` 统一管理 |
| 历史保留 | 无限制保留所有版本 |
| 版本切换 UI | 预览区上方下拉选择器 |
| 宫格图 | 不纳入版本管理（仅 `scene_*.png` 需要版本控制） |

---

## 数据结构设计

### 1. 版本目录结构

```
projects/{项目名}/
├── versions/                    # 集中版本目录
│   ├── versions.json            # 版本元数据
│   ├── storyboards/             # 分镜图历史版本（仅 scene_*.png）
│   │   ├── E1S01_v1_20260129T103045.png
│   │   ├── E1S01_v2_20260129T114530.png
│   │   └── ...
│   ├── videos/                  # 视频历史版本
│   │   ├── E1S01_v1_20260129T120000.mp4
│   │   └── ...
│   ├── characters/              # 角色图历史版本
│   │   ├── 姜月茴_v1_20260129T090000.png
│   │   └── ...
│   └── clues/                   # 线索图历史版本
│       ├── 玉佩_v1_20260129T091500.png
│       └── ...
├── storyboards/                 # 当前版本（保持原路径）
│   ├── scene_E1S01.png          # ✓ 需要版本控制
│   ├── grid_001.png             # ✗ 宫格图不需要版本控制
│   └── ...
├── videos/
├── characters/
└── clues/
```

### 2. versions.json 结构

```json
{
  "storyboards": {
    "E1S01": {
      "current_version": 2,
      "versions": [
        {
          "version": 1,
          "file": "storyboards/E1S01_v1_20260129T103045.png",
          "prompt": "中景镜头，姜府后花园...",
          "created_at": "2026-01-29T10:30:45Z",
          "aspect_ratio": "9:16"
        },
        {
          "version": 2,
          "file": "storyboards/E1S01_v2_20260129T114530.png",
          "prompt": "中景镜头，修改后的描述...",
          "created_at": "2026-01-29T11:45:30Z",
          "aspect_ratio": "9:16"
        }
      ]
    }
  },
  "videos": {
    "E1S01": {
      "current_version": 1,
      "versions": [
        {
          "version": 1,
          "file": "videos/E1S01_v1_20260129T120000.mp4",
          "prompt": "镜头缓慢推进...",
          "created_at": "2026-01-29T12:00:00Z",
          "duration_seconds": 4
        }
      ]
    }
  },
  "characters": {
    "姜月茴": {
      "current_version": 1,
      "versions": [
        {
          "version": 1,
          "file": "characters/姜月茴_v1_20260129T090000.png",
          "prompt": "二十出头女子，鹅蛋脸，柳叶眉...",
          "created_at": "2026-01-29T09:00:00Z"
        }
      ]
    }
  },
  "clues": {
    "玉佩": {
      "current_version": 1,
      "versions": [
        {
          "version": 1,
          "file": "clues/玉佩_v1_20260129T091500.png",
          "prompt": "翠绿色祖传玉佩，雕刻着莲花纹样...",
          "created_at": "2026-01-29T09:15:00Z"
        }
      ]
    }
  }
}
```

**说明**：
- `storyboards` 和 `videos` 的 key 使用 segment/scene ID（如 `E1S01`）
- `characters` 和 `clues` 的 key 使用名称
- `grid_*.png` 宫格图不纳入版本管理

---

## 后端 API 设计

### 1. API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/api/v1/projects/{name}/generate/storyboard/{segment_id}` | 生成分镜图（首次或新版本） |
| `POST` | `/api/v1/projects/{name}/generate/video/{segment_id}` | 生成视频（首次或新版本） |
| `POST` | `/api/v1/projects/{name}/generate/character/{char_name}` | 生成角色设计图 |
| `POST` | `/api/v1/projects/{name}/generate/clue/{clue_name}` | 生成线索设计图 |
| `GET` | `/api/v1/projects/{name}/versions/{resource_type}/{resource_id}` | 获取资源版本列表 |
| `POST` | `/api/v1/projects/{name}/versions/{resource_type}/{resource_id}/restore/{version}` | 还原到指定版本 |

### 2. 生成请求/响应

```json
// POST /api/v1/projects/{name}/generate/storyboard/{segment_id}
// 请求体
{
  "prompt": "image_prompt 文本",
  "script_file": "episode_1.json"
}

// 响应
{
  "success": true,
  "version": 1,
  "file_path": "storyboards/scene_E1S01.png",
  "created_at": "2026-01-29T11:45:30Z"
}
```

### 3. 版本列表响应

```json
// GET /api/v1/projects/{name}/versions/storyboards/E1S01
{
  "resource_type": "storyboards",
  "resource_id": "E1S01",
  "current_version": 2,
  "versions": [
    {
      "version": 1,
      "file": "versions/storyboards/E1S01_v1_20260129T103045.png",
      "file_url": "/api/v1/files/{name}/versions/storyboards/E1S01_v1_20260129T103045.png",
      "prompt": "中景镜头，姜府后花园...",
      "created_at": "2026-01-29T10:30:45Z"
    },
    {
      "version": 2,
      "file": "versions/storyboards/E1S01_v2_20260129T114530.png",
      "file_url": "/api/v1/files/{name}/versions/storyboards/...",
      "prompt": "修改后的描述...",
      "created_at": "2026-01-29T11:45:30Z",
      "is_current": true
    }
  ]
}
```

### 4. 还原响应

```json
// POST /api/v1/projects/{name}/versions/storyboards/E1S01/restore/1
{
  "success": true,
  "restored_version": 1,
  "new_current_version": 3,
  "prompt": "原始描述文本..."
}
```

### 5. 生成核心逻辑

```python
async def generate_storyboard(name, segment_id, prompt, script_file):
    current_file = f"storyboards/scene_{segment_id}.png"

    if current_file exists:
        # 有现有文件 → 备份到版本目录
        backup_to_versions(current_file, prompt)

    # 调用 GeminiClient 生成新图片
    new_image = await gemini_client.generate_image_async(
        prompt=prompt,
        reference_images=get_character_refs(segment_id),
        aspect_ratio=get_aspect_ratio(project),
        output_path=current_file
    )

    # 更新 versions.json（新增版本记录）
    add_version_record(segment_id, prompt)

    # 更新 script 中的 generated_assets
    update_script_assets(script_file, segment_id, current_file)

    return {"success": True, "version": new_version, ...}
```

---

## 前端 UI 设计

### 1. 编辑模态框改造

在片段/场景、角色、线索的编辑模态框中，预览区域增加：
- **版本下拉选择器**：显示在预览图上方
- **生成按钮**：无图时显示「生成」，有图时显示「重新生成」

```
┌─────────────────────────────────────┐
│  分镜图预览                          │
│  ┌─────────────────────┬──────────┐ │
│  │ 版本: [v2 当前 ▼]    │ 🔄 重新生成│ │
│  └─────────────────────┴──────────┘ │
│  ┌─────────────────────────────────┐ │
│  │                                 │ │
│  │         (图片预览区)             │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│  版本 prompt: 中景镜头，姜府后花园... │
└─────────────────────────────────────┘
```

### 2. 版本下拉选择器行为

- 切换版本时：预览区显示对应版本的图片，下方显示该版本的 prompt（只读）
- 当前编辑框中的 prompt 保持独立（用于生成新版本）
- 选择非当前版本时，显示「还原此版本」按钮

### 3. 生成按钮状态

| 状态 | 按钮文案 | 样式 |
|------|---------|------|
| 无图片 | 生成 | 绿色主按钮 |
| 有图片 | 重新生成 | 蓝色按钮 |
| 生成中 | ⏳ 生成中... | 灰色禁用 + loading 动画 |

### 4. 还原交互

当用户选择非当前版本时：

```
┌─────────────────────────────────────┐
│  分镜图预览                          │
│  ┌─────────────────────┬──────────┐ │
│  │ 版本: [v1 ▼]         │ ↩️ 还原   │ │
│  └─────────────────────┴──────────┘ │
│  ┌─────────────────────────────────┐ │
│  │                                 │ │
│  │    (v1 版本图片预览)             │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│  历史 prompt: 原始描述文本...        │
└─────────────────────────────────────┘
```

点击「还原」后：
1. 将当前版本备份到 `versions/` 目录
2. 将选中的历史版本文件复制到当前路径
3. 更新 `versions.json` 的 `current_version`
4. 将历史版本的 prompt 填充到编辑框
5. 刷新预览和版本列表

---

## 文件结构与实现模块

### 1. 新增/修改的文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `lib/version_manager.py` | 新增 | 版本管理核心逻辑（备份、还原、记录） |
| `webui/server/routers/generate.py` | 新增 | 生成 API 路由 |
| `webui/server/routers/versions.py` | 新增 | 版本管理 API 路由 |
| `webui/server/app.py` | 修改 | 注册新路由 |
| `webui/js/api.js` | 修改 | 添加生成和版本相关 API 调用 |
| `webui/js/project.js` | 修改 | 添加版本选择器、生成按钮交互逻辑 |
| `webui/project.html` | 修改 | 更新模态框 UI（版本选择器、生成按钮） |
| `webui/css/styles.css` | 修改 | 添加版本选择器和 loading 状态样式 |

### 2. lib/version_manager.py 核心类

```python
class VersionManager:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.versions_dir = project_path / "versions"
        self.versions_file = self.versions_dir / "versions.json"

    def get_versions(self, resource_type: str, resource_id: str) -> dict:
        """获取资源的所有版本信息"""
        pass

    def add_version(self, resource_type: str, resource_id: str,
                    file_path: str, prompt: str, **metadata) -> int:
        """添加新版本记录，返回版本号"""
        pass

    def backup_current(self, resource_type: str, resource_id: str,
                       current_file: Path, prompt: str) -> None:
        """将当前文件备份到版本目录"""
        pass

    def restore_version(self, resource_type: str, resource_id: str,
                        version: int) -> dict:
        """还原到指定版本，返回还原信息"""
        pass

    def get_current_version(self, resource_type: str, resource_id: str) -> int:
        """获取当前版本号"""
        pass
```

---

## 实现优先级

| 阶段 | 内容 | 预估工作量 |
|------|------|-----------|
| Phase 1 | `VersionManager` + 生成 API（支持首次生成和重新生成） | 中 |
| Phase 2 | 前端生成按钮和 loading 状态 | 小 |
| Phase 3 | 版本列表 API + 前端版本选择器 | 中 |
| Phase 4 | 还原 API + 前端还原交互 | 小 |

---

## 注意事项

1. **宫格图排除**：`grid_*.png` 不纳入版本管理，仅处理 `scene_*.png`
2. **参考图传递**：生成分镜图/视频时，自动获取 segment/scene 中的 `characters_in_segment` 和 `clues_in_segment`，传递对应的设计图作为参考
3. **画面比例**：根据 `content_mode` 自动选择（narration: 9:16, drama: 16:9）
4. **并发安全**：`versions.json` 读写需要加锁，防止并发冲突
5. **错误处理**：API 调用失败时保留原文件，不影响现有版本
