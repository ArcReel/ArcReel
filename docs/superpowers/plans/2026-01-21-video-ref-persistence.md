# 视频引用持久化方案实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 Veo 视频引用的持久化存储，使得在多步任务中可以继续延长之前生成的视频。

**Architecture:** 将视频生成后返回的 `video.uri` 保存到 checkpoint JSON 文件中。当需要恢复时，使用 `types.Video(uri=saved_uri)` 重建 Video 对象，然后继续调用 extend API。

**Tech Stack:** Python, google-genai SDK, JSON 文件存储

**关键发现：**
- `types.Video` 对象有 `uri` 字段，包含 Gemini 服务器上的视频 URI
- 视频在服务器保存 2 天，每次 extend 会重置计时器
- 可以通过 `types.Video(uri=saved_uri)` 重建 Video 对象

**重要限制：**
- ⚠️ Veo extend 目前只支持 16:9 横屏视频（API 返回错误：9:16 不被支持）
- 需要决定是改用 16:9 格式还是等待 API 更新

---

## Task 1: 更新 Checkpoint 数据结构

**Files:**
- Modify: `.claude/skills/generate-video/scripts/generate_video.py:105-127`

**Step 1: 修改 checkpoint 结构，添加 video_uri 字段**

更新 `save_checkpoint()` 函数，添加 `video_uri` 参数：

```python
def save_checkpoint(
    project_dir: Path,
    episode: int,
    current_segment: int,
    current_scene_index: int,
    completed_segments: list,
    started_at: str,
    video_uri: Optional[str] = None  # 新增：视频 URI 用于恢复
):
    """保存 checkpoint，包含视频引用 URI"""
    checkpoint_path = get_checkpoint_path(project_dir, episode)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "episode": episode,
        "current_segment": current_segment,
        "current_scene_index": current_scene_index,
        "completed_segments": completed_segments,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(),
        "video_uri": video_uri,  # 新增：保存视频 URI
        "video_uri_expires_at": (datetime.now() + timedelta(days=2)).isoformat() if video_uri else None
    }

    with open(checkpoint_path, 'w', encoding='utf-8') as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
```

**Step 2: 添加 timedelta 导入**

在文件顶部添加：

```python
from datetime import datetime, timedelta
```

**Step 3: 验证语法**

Run: `python -m py_compile .claude/skills/generate-video/scripts/generate_video.py`
Expected: 无输出（成功）

**Step 4: Commit**

```bash
git add .claude/skills/generate-video/scripts/generate_video.py
git commit -m "feat: add video_uri field to checkpoint for resume support"
```

---

## Task 2: 添加 Video URI 恢复功能到 GeminiClient

**Files:**
- Modify: `lib/gemini_client.py` (在 `extend_video` 方法后添加新方法)

**Step 1: 添加 restore_video_ref() 方法**

在 `extend_video()` 方法后添加：

```python
def restore_video_ref(self, video_uri: str):
    """
    从保存的 URI 恢复视频引用对象

    Args:
        video_uri: 之前保存的视频 URI（如 "https://generativelanguage.googleapis.com/..."）

    Returns:
        types.Video 对象，可用于 extend_video()

    Note:
        - 视频在服务器保存 2 天
        - 每次 extend 会重置 2 天计时器
        - 如果视频已过期，将抛出异常
    """
    if not video_uri:
        raise ValueError("video_uri 不能为空")

    return self.types.Video(uri=video_uri)
```

**Step 2: 验证语法**

Run: `python -m py_compile lib/gemini_client.py`
Expected: 无输出（成功）

**Step 3: 测试导入**

Run: `PYTHONPATH=. python -c "from lib.gemini_client import GeminiClient; c = GeminiClient(); print('restore_video_ref 存在:', hasattr(c, 'restore_video_ref'))"`
Expected: `restore_video_ref 存在: True`

**Step 4: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat: add restore_video_ref() method for resuming video extensions"
```

---

## Task 3: 更新 generate_video_with_ref 返回视频 URI

**Files:**
- Modify: `lib/gemini_client.py:275-353` (`generate_video_with_ref` 方法)

**Step 1: 修改返回值，包含 video_uri**

将返回语句从：

```python
return output_path, video_ref
```

改为：

```python
return output_path, video_ref, video_ref.uri
```

同时更新方法签名的返回类型文档：

```python
def generate_video_with_ref(
    ...
) -> tuple:
    """
    生成视频并返回视频引用，用于后续扩展

    ...

    Returns:
        (output_path, video_ref, video_uri) 三元组
        - output_path: 视频文件路径
        - video_ref: Video 对象，用于当前会话的 extend_video()
        - video_uri: 字符串 URI，可保存用于跨会话恢复
    """
```

**Step 2: 验证语法**

Run: `python -m py_compile lib/gemini_client.py`
Expected: 无输出（成功）

**Step 3: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat: return video_uri from generate_video_with_ref for persistence"
```

---

## Task 4: 更新 extend_video 返回视频 URI

**Files:**
- Modify: `lib/gemini_client.py:355-432` (`extend_video` 方法)

**Step 1: 修改返回值，包含 video_uri**

将返回语句从：

```python
return output_path, new_video_ref
```

改为：

```python
return output_path, new_video_ref, new_video_ref.uri
```

同时更新方法签名的返回类型文档：

```python
def extend_video(
    ...
) -> tuple:
    """
    扩展现有视频（每次 +7 秒，最多扩展 20 次）

    ...

    Returns:
        (output_path, new_video_ref, new_video_uri) 三元组
        - output_path: 扩展后的视频文件路径
        - new_video_ref: 新的 Video 对象，用于继续扩展
        - new_video_uri: 字符串 URI，可保存用于跨会话恢复
    """
```

**Step 2: 验证语法**

Run: `python -m py_compile lib/gemini_client.py`
Expected: 无输出（成功）

**Step 3: Commit**

```bash
git add lib/gemini_client.py
git commit -m "feat: return video_uri from extend_video for persistence"
```

---

## Task 5: 更新 generate_continuous_video 以保存和恢复视频 URI

**Files:**
- Modify: `.claude/skills/generate-video/scripts/generate_video.py:218-369`

**Step 1: 更新视频生成逻辑以保存 URI**

在 `generate_continuous_video()` 函数中修改视频生成部分：

```python
# 在 for scene_idx, scene in enumerate(segment) 循环内

try:
    if video_ref is None:
        # 第一个场景：使用 image-to-video
        print(f"    🎥 生成初始视频（{duration}秒）...")
        output_path, video_ref, video_uri = client.generate_video_with_ref(
            prompt=prompt,
            start_image=storyboard_path,
            aspect_ratio="16:9",  # 注意：extend 只支持 16:9
            duration_seconds=str(duration),
            resolution="720p",
            output_path=segment_output
        )
    else:
        # 后续场景：使用 extend
        print(f"    🔗 扩展视频（+7秒）...")
        output_path, video_ref, video_uri = client.extend_video(
            video_ref=video_ref,
            prompt=prompt,
            output_path=segment_output
        )

    # 保存 checkpoint（包含 video_uri）
    save_checkpoint(
        project_dir, episode,
        seg_idx, scene_idx + 1,
        segment_videos, started_at,
        video_uri=video_uri  # 保存 URI 用于恢复
    )
```

**Step 2: 添加恢复逻辑**

在加载 checkpoint 后添加恢复逻辑：

```python
# 在 if resume: 块内，checkpoint 加载后
if resume:
    checkpoint = load_checkpoint(project_dir, episode)
    if checkpoint:
        start_segment = checkpoint.get('current_segment', 0)
        completed_segments = checkpoint.get('completed_segments', [])
        started_at = checkpoint.get('started_at', started_at)

        # 恢复视频引用
        saved_uri = checkpoint.get('video_uri')
        if saved_uri:
            expires_at = checkpoint.get('video_uri_expires_at')
            if expires_at:
                expires = datetime.fromisoformat(expires_at)
                if datetime.now() < expires:
                    video_ref = client.restore_video_ref(saved_uri)
                    print(f"🔄 从 checkpoint 恢复视频引用")
                else:
                    print(f"⚠️ 视频引用已过期，将从该片段重新生成")
                    video_ref = None

        print(f"🔄 从片段 {start_segment + 1} 继续")
    else:
        print("⚠️  未找到 checkpoint，从头开始")
```

**Step 3: 验证语法**

Run: `python -m py_compile .claude/skills/generate-video/scripts/generate_video.py`
Expected: 无输出（成功）

**Step 4: Commit**

```bash
git add .claude/skills/generate-video/scripts/generate_video.py
git commit -m "feat: save and restore video_uri in continuous video generation"
```

---

## Task 6: 更新文档说明视频引用持久化

**Files:**
- Modify: `.claude/skills/generate-video/SKILL.md`
- Modify: `CLAUDE.md`

**Step 1: 更新 SKILL.md 添加持久化说明**

在 "断点续传" 部分后添加：

```markdown
### 视频引用持久化

连续视频模式会自动保存视频引用（URI）到 checkpoint 文件：

- 保存位置：`projects/{项目名}/videos/.checkpoint_ep{N}.json`
- 视频在 Gemini 服务器保存 2 天
- 每次 extend 会重置 2 天计时器
- 使用 `--resume` 时自动恢复视频引用

**注意事项：**
- 如果超过 2 天未继续，视频引用将过期
- 过期后需要从该片段重新生成
- 建议在开始生成后尽快完成整集
```

**Step 2: 更新 CLAUDE.md 添加相关说明**

在 "断点续传" 部分添加：

```markdown
### 视频引用保存

Checkpoint 文件会保存视频引用 URI，有效期 2 天：

```json
{
  "episode": 1,
  "current_segment": 0,
  "current_scene_index": 3,
  "video_uri": "https://generativelanguage.googleapis.com/...",
  "video_uri_expires_at": "2026-01-23T12:00:00"
}
```
```

**Step 3: Commit**

```bash
git add .claude/skills/generate-video/SKILL.md CLAUDE.md
git commit -m "docs: add video reference persistence documentation"
```

---

## Task 7: 添加 16:9 格式支持说明

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/skills/generate-video/SKILL.md`

**Step 1: 更新 CLAUDE.md 视频规格说明**

修改 "视频规格" 部分：

```markdown
### 视频规格
- **视频比例**：16:9 横屏格式（Veo extend 限制）
- **单场景时长**：默认 8 秒
- **扩展时长**：每次 +7 秒
- **连续视频最大时长**：148 秒（约 2.5 分钟）
- **分辨率**：720p（扩展模式限制）
- **分镜图格式**：多宫格分镜图（16:9 横屏，自适应 2x2 或 2x3 布局）

> ⚠️ **重要**：Veo extend API 目前只支持 16:9 横屏视频，9:16 竖屏视频无法扩展。
> 如需 9:16 竖屏格式，可在后期处理时使用 ffmpeg 裁剪转换。
```

**Step 2: 更新 SKILL.md 添加格式限制说明**

在 "Veo 3.1 扩展限制" 表格中添加：

```markdown
| 宽高比限制 | 仅 16:9 横屏 |
```

并添加说明：

```markdown
> ⚠️ **API 限制**：虽然文档说支持 9:16 和 16:9，但实际测试发现 extend API 只接受 16:9 横屏视频。
> 9:16 竖屏视频会返回错误：`Aspect ratio of the input video must be 16:9`
```

**Step 3: Commit**

```bash
git add CLAUDE.md .claude/skills/generate-video/SKILL.md
git commit -m "docs: clarify 16:9 aspect ratio requirement for Veo extend"
```

---

## Task 8: 验证完整流程

**Files:** 无修改，仅验证

**Step 1: 验证脚本语法**

Run:
```bash
python -m py_compile lib/gemini_client.py
python -m py_compile .claude/skills/generate-video/scripts/generate_video.py
```
Expected: 无输出（成功）

**Step 2: 验证 CLI 帮助**

Run: `PYTHONPATH=. python .claude/skills/generate-video/scripts/generate_video.py --help`
Expected: 显示帮助信息，包含 `--continuous`, `--episode`, `--resume` 选项

**Step 3: 验证 segment 分组**

Run:
```bash
PYTHONPATH=. python -c "
import json
from pathlib import Path

script = json.load(open('projects/shanyang_renlei/scripts/episode_01.json'))
scenes = [s for s in script['scenes'] if s.get('episode', 1) == 1]

segments = []
current = []
for s in scenes:
    if s.get('segment_break') and current:
        segments.append(current)
        current = []
    current.append(s)
if current:
    segments.append(current)

print(f'场景数: {len(scenes)}')
print(f'片段数: {len(segments)}')
for i, seg in enumerate(segments):
    print(f'  片段 {i+1}: {len(seg)} 场景')
"
```
Expected: 显示 22 个场景，4 个片段

**Step 4: 最终 Commit**

```bash
git add -A
git commit -m "feat: complete video reference persistence implementation"
```

---

## 可选：Task 9: 实际 API 测试

**注意：此任务需要消耗 API 配额，可选执行**

**Step 1: 生成第一个视频并保存 checkpoint**

```bash
PYTHONPATH=. python -c "
from lib.gemini_client import GeminiClient
from pathlib import Path
import json

client = GeminiClient()
project_dir = Path('projects/shanyang_renlei')

# 生成视频
path, ref, uri = client.generate_video_with_ref(
    prompt='一段 6 秒的横屏视频（16:9）。夜晚都市，五星级酒店外观。',
    start_image=project_dir / 'storyboards/grid_001.png',
    aspect_ratio='16:9',
    duration_seconds='6',
    resolution='720p',
    output_path=project_dir / 'videos/test_persist.mp4'
)

print(f'视频生成成功: {path}')
print(f'URI: {uri}')

# 保存 URI
(project_dir / 'videos/test_uri.txt').write_text(uri)
print('URI 已保存')
"
```

**Step 2: 恢复并扩展视频**

```bash
PYTHONPATH=. python -c "
from lib.gemini_client import GeminiClient
from pathlib import Path

client = GeminiClient()
project_dir = Path('projects/shanyang_renlei')

# 读取保存的 URI
uri = (project_dir / 'videos/test_uri.txt').read_text().strip()
print(f'读取 URI: {uri[:50]}...')

# 恢复视频引用
video_ref = client.restore_video_ref(uri)
print('视频引用已恢复')

# 扩展视频
path, ref, new_uri = client.extend_video(
    video_ref=video_ref,
    prompt='继续：酒店大厅内部，水晶吊灯，猩红地毯，一个穿黑色皮夹克的男子走入。',
    output_path=project_dir / 'videos/test_persist_extended.mp4'
)

print(f'扩展成功: {path}')
"
```

Expected: 两个视频文件生成成功，扩展后的视频时长约 13 秒

---

## 总结

实现完成后，视频引用持久化工作流程：

1. **首次生成**：`generate_video_with_ref()` 返回 `(path, video_ref, video_uri)`
2. **保存 URI**：`save_checkpoint(..., video_uri=video_uri)`
3. **中断后恢复**：`load_checkpoint()` 读取 `video_uri`
4. **重建引用**：`restore_video_ref(video_uri)` 返回 `video_ref`
5. **继续扩展**：`extend_video(video_ref, ...)`

有效期：2 天（每次 extend 重置）
