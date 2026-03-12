# 媒体缓存与视频缩略图优化设计

## 背景

当前 ArcReel 时间线在滚动（虚拟滚动卸载/重挂载）和重进页面时，图片/视频会重复下载。
根因是：前端 `entityRevisions` 是 session 级计数器，后端 `FileResponse` 无缓存头。

## 目标

1. **零网络请求缓存**：文件内容未变时，滚动回来和跨 session 重进均从浏览器 disk cache 加载
2. **视频带宽优化**：视频默认不预加载，用首帧缩略图作为封面，点击后才加载
3. **版本浏览缓存**：版本快照文件设 immutable 缓存，版本切换即时刷新

## 设计

### Part 1：基于文件指纹的内容寻址缓存

#### 核心思路

用文件 `mtime`（修改时间）作为 URL cache-bust 参数，替代 session 级计数器。
文件不变 → mtime 不变 → URL 不变 → 浏览器 disk cache 命中 → 零网络。

#### 后端

**1) 项目 API 返回 `asset_fingerprints`**

`GET /api/v1/projects/{name}` 响应新增顶层字段：

```json
{
  "project": { "..." },
  "scripts": { "..." },
  "asset_fingerprints": {
    "storyboards/scene_E1S01.png": 1710288000,
    "videos/scene_E1S01.mp4": 1710289000,
    "thumbnails/scene_E1S01.jpg": 1710289000,
    "characters/角色名.png": 1710287000
  }
}
```

实现：扫描项目目录下 `storyboards/`、`videos/`、`thumbnails/`、`characters/`、`clues/`，
用 `int(file.stat().st_mtime)` 生成指纹 map。~50 个文件耗时 <1ms。

**2) SSE 事件携带 `asset_fingerprints`**

在 `_emit_generation_success_batch()` 中，生成完成后计算受影响文件的 mtime：

```json
{
  "entity_type": "segment",
  "action": "storyboard_ready",
  "entity_id": "S1",
  "label": "分镜「S1」",
  "asset_fingerprints": {
    "storyboards/scene_S1.png": 1710289000
  }
}
```

好处：fingerprint 随 SSE 事件即时到达，前端无需额外 API 调用就能更新 URL。

**3) 文件路由设置 immutable 缓存头**

`GET /api/v1/files/{project}/{path}` 响应头：

```
有 ?v= 参数 或 路径包含 versions/  →  Cache-Control: public, max-age=31536000, immutable
其他                                →  无特殊缓存头
```

#### 前端

**4) 新增 fingerprint 状态管理**

在 projects-store 中新增：

```typescript
assetFingerprints: Record<string, number>;
updateAssetFingerprints: (fps: Record<string, number>) => void;
getAssetFingerprint: (path: string) => number | null;
```

初始加载时从项目 API 响应设置；SSE 事件到达时增量更新。

**5) SSE 处理优化**

```typescript
onChanges(payload) {
  // 立即更新 fingerprints
  for (const change of payload.changes) {
    if (change.asset_fingerprints) {
      updateAssetFingerprints(change.asset_fingerprints);
    }
  }

  // 仅在结构性变更时 refreshProject()
  const needsRefresh = payload.changes.some(c =>
    ["created", "deleted"].includes(c.action) ||
    ["episode", "project", "overview"].includes(c.entity_type)
  );
  if (needsRefresh) void refreshProject();
}
```

首次生成（`generated_assets` 从 null → 路径）仍需 refreshProject() 获取脚本更新。

**6) URL 构建使用 fingerprint**

```typescript
const fp = useProjectsStore(s => s.getAssetFingerprint(assetPath));
const url = API.getFileUrl(projectName, assetPath, fp);
// → "/api/v1/files/MyProject/storyboards/scene_E1S01.png?v=1710288000"
```

#### 缓存命中场景

| 场景 | 旧方案 | 新方案 |
|------|--------|--------|
| 虚拟滚动卸载再挂载 | 重新下载 | disk cache，零网络 |
| 刷新页面 | ?v=N 重新下载 | 同 mtime → 同 URL → 缓存命中 |
| 新 session 打开 | ?v=0 内容可能已变 | 同 mtime → 同 URL → 缓存命中 |
| 文件重新生成 | revision+1 | mtime 变 → URL 变 → 重下 |

### Part 2：视频首帧缩略图

#### 生成时机

在视频生成完成后（`execute_video_task` 中），由同一 worker 用 ffmpeg 提取首帧：

```python
thumbnail_path = project_path / "thumbnails" / f"scene_{resource_id}.jpg"
thumbnail_path.parent.mkdir(exist_ok=True)

await asyncio.create_subprocess_exec(
    "ffmpeg", "-i", str(video_path),
    "-vframes", "1", "-q:v", "2",
    "-y", str(thumbnail_path)
)
```

#### 存储结构

```
projects/{project_name}/
├── videos/scene_E1S01.mp4
├── thumbnails/scene_E1S01.jpg        ← 新增：视频首帧
├── storyboards/scene_E1S01.png
└── versions/
    └── thumbnails/                    ← 新增：版本视频首帧
        └── E1S01_v1_20260312T103045.jpg
```

#### 数据模型扩展

`generated_assets` 新增 `video_thumbnail` 字段：

```json
{
  "storyboard_image": "storyboards/scene_E1S01.png",
  "video_clip": "videos/scene_E1S01.mp4",
  "video_thumbnail": "thumbnails/scene_E1S01.jpg",
  "video_uri": "...",
  "status": "completed"
}
```

#### 前端使用

```tsx
<video
  poster={thumbnailUrl}
  preload="none"
  src={videoUrl}
  controls
  playsInline
/>
```

#### SSE 事件包含缩略图 fingerprint

```json
{
  "action": "video_ready",
  "asset_fingerprints": {
    "videos/scene_E1S01.mp4": 1710289000,
    "thumbnails/scene_E1S01.jpg": 1710289000
  }
}
```

### Part 3：版本浏览与切换的缓存适配

#### 版本文件缓存

版本文件（`versions/` 下）是不可变快照，URL 包含版本号+时间戳，天然唯一。
后端检测路径包含 `versions/` 时直接设 `immutable` 缓存头。

#### 版本视频缩略图

在 `VersionManager.add_version()` 中，对视频版本文件也提取首帧：
- 存储在 `versions/thumbnails/{resource_id}_v{N}_{timestamp}.jpg`
- VersionTimeMachine 中的视频预览也使用 `preload="none"` + poster

#### 版本切换刷新

`restore_version()` API 返回新的 `asset_fingerprints`：

```python
return {
    "success": True,
    **result,
    "file_path": file_path,
    "asset_fingerprints": {
        file_path: int(current_file.stat().st_mtime)
    }
}
```

前端直接用返回的 fingerprint 更新 store，主显示区 URL 即时变化。

## 涉及文件

### 后端

| 文件 | 改动 |
|------|------|
| `server/routers/projects.py` | 项目 API 返回 `asset_fingerprints` |
| `server/routers/files.py` | 添加 `Cache-Control: immutable` 响应头 |
| `server/routers/versions.py` | restore API 返回 `asset_fingerprints` |
| `server/services/generation_tasks.py` | SSE 事件携带 `asset_fingerprints` |
| `server/services/project_events.py` | ProjectChange 类型扩展（可选） |
| `lib/media_generator.py` | 视频生成后提取首帧缩略图 |
| `lib/version_manager.py` | 版本保存时为视频提取缩略图 |
| `lib/project_manager.py` | `create_generated_assets` 新增 `video_thumbnail` 字段 |

### 前端

| 文件 | 改动 |
|------|------|
| `frontend/src/stores/projects-store.ts` | 新增 fingerprint 状态管理 |
| `frontend/src/hooks/useProjectEventsSSE.ts` | SSE 处理使用 fingerprint |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | URL 用 fingerprint；视频用 poster + preload=none |
| `frontend/src/components/canvas/timeline/VersionTimeMachine.tsx` | 视频用 poster + preload=none；还原用 fingerprint |
| `frontend/src/components/canvas/lorebook/CharacterCard.tsx` | URL 用 fingerprint |
| `frontend/src/components/canvas/lorebook/ClueCard.tsx` | URL 用 fingerprint |
| `frontend/src/components/canvas/OverviewCanvas.tsx` | URL 用 fingerprint |
| `frontend/src/components/ui/AvatarStack.tsx` | URL 用 fingerprint |
| `frontend/src/api.ts` | VersionInfo 类型扩展 |
| `frontend/src/types/workspace.ts` | ProjectChange 类型扩展 |
