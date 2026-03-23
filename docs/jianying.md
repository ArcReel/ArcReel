# 剪映草稿导出功能设计文档

**日期**：2026-03-23
**状态**：草案

---

## 背景与动机

ArcReel 当前的成片输出方式是通过 FFmpeg 将各场景视频片段拼接为完整视频（`compose_video.py`），用户无法在此基础上做精细剪辑。许多用户习惯使用剪映（JianYing）进行后期处理——调整节奏、添加字幕、转场、配音等。

本功能允许用户将 ArcReel 中已生成的视频片段**按集导出为剪映草稿文件**，在剪映桌面版中直接打开并继续编辑，打通"AI 生成 → 人工精剪"的工作流。

### 设计目标

- **按集导出**：用户选择某一集，导出该集所有已生成视频片段的剪映草稿
- **最简拼接**：仅实现视频素材在时间线上的顺序排列，不涉及特效、转场、字幕等高级功能
- **素材自包含**：导出 ZIP 包内含草稿 JSON + 视频文件，用户解压到剪映草稿目录即可使用
- **复用现有导出机制**：沿用项目已有的下载 token + 浏览器原生下载模式

### 非目标

- 不支持剪映模板模式（读取/修改已有草稿）——剪映 6+ 加密了 `draft_content.json`
- 不支持远程 URL 素材引用——剪映仅支持本地文件路径
- 不开发桌面 Helper 工具（MVP 阶段）
- 不支持 CapCut 国际版（可作为后续扩展）
- 不导出音频轨（BGM、配音）——MVP 仅处理视频轨

---

## 技术选型：pyJianYingDraft

### 选型结论

**直接集成 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) 库**（`pip install pyjianyingdraft`），而非自行实现 JSON 生成逻辑。

### 选型理由

| 维度 | pyJianYingDraft | 自行实现 |
|------|----------------|---------|
| 成熟度 | 2800+ Star，500+ Fork，持续维护 2 年+ | 需从零构建、自行跟进剪映格式变更 |
| 安装方式 | `pip install pyjianyingdraft` | 无 |
| 语言 | Python，与 ArcReel 后端一致 | — |
| 依赖 | `pymediainfo`（提取媒体元数据）+ `imageio` | 需自行提取视频时长/分辨率 |
| 兼容性 | 剪映 5+ 所有版本（创建新草稿） | 需自行验证 |
| 维护成本 | 社区跟进剪映版本变更 | 完全自负 |
| Python 版本 | ≥ 3.8，兼容 ArcReel 的 3.12+ | — |

### 系统依赖

Docker 部署时需在 Dockerfile 中添加 `mediainfo` 系统库：

```dockerfile
RUN apt-get update && apt-get install -y mediainfo && rm -rf /var/lib/apt/lists/*
```

### 备选方案参考

调研了三个开源项目，各有侧重：

- **capcut-mate**（222 Star）：FastAPI 架构，已实现远程 URL 素材下载 + 路径替换，架构参考价值高
- **pyJianYingDraft**（2800+ Star）：最成熟的草稿生成库，API 简洁，pip 可安装 → **选定**
- **jianying-editor-skill**（515 Star）：AI Agent 集成思路参考，"文件注入驱动"模式有启发性

---

## 数据模型映射

### ArcReel 数据结构 → 剪映草稿结构

ArcReel 的剧本以集为单位，每集包含多个场景/片段（scenes/segments），每个场景的 `generated_assets` 中存储已生成的视频文件路径。

```
ArcReel 剧本（episode_N.json）          剪映草稿（draft_content.json）
─────────────────────────────          ──────────────────────────────
scenes[]/segments[]                →   materials.videos[]
  ├── scene_id: "E1S01"                  ├── id: <uuid>
  ├── duration_seconds: 8                ├── duration: 8000000  (微秒)
  └── generated_assets:                  ├── path: "/absolute/path/scene_E1S01.mp4"
      ├── video_clip: "videos/..."       ├── width: 1920
      └── status: "completed"            └── height: 1080

                                   →   tracks[0].segments[]
                                         ├── material_id: <对应 material uuid>
                                         ├── source_timerange: {start, duration}
                                         └── target_timerange: {start, duration}
```

**时间单位转换**：ArcReel 使用秒（`duration_seconds: 8`），剪映使用微秒（`duration: 8000000`）。转换公式：`jianying_us = arcreel_s × 1_000_000`。

### 项目文件结构参考

```
projects/{project_name}/
├── scripts/
│   └── episode_1.json          # 剧本（包含 scenes/segments + generated_assets）
├── videos/
│   ├── scene_E1S01.mp4         # 各场景视频片段
│   ├── scene_E1S02.mp4
│   └── ...
├── storyboards/                # 分镜图（不导出到草稿）
├── thumbnails/                 # 视频缩略图（不导出到草稿）
└── output/                     # FFmpeg 合成的最终视频
```

---

## 导出流程设计

### 整体流程

```
用户点击"导出剪映草稿"
    ↓
选择集数（下拉/弹窗）
    ↓
前端 → POST /api/v1/projects/{name}/export/jianying-draft/token
    ↓  （签发下载 token，复用现有机制）
前端 → window.open(GET /api/v1/projects/{name}/export/jianying-draft?episode=N&download_token=xxx)
    ↓
后端：
  1. 加载剧本，筛选已完成视频的场景
  2. 创建临时目录
  3. 复制视频文件到临时目录（模拟剪映草稿的 assets/ 结构）
  4. 调用 pyJianYingDraft 生成 draft_content.json + draft_meta_info.json
  5. 打包为 ZIP 返回
    ↓
用户下载 ZIP → 解压到剪映草稿目录 → 重启剪映
```

### 导出 ZIP 包结构

```
{项目名}_E{集数}_剪映草稿.zip
└── {项目名}_第{N}集/                    # 草稿文件夹名（即剪映中显示的草稿名称）
    ├── draft_content.json              # 核心草稿文件（时间线数据）
    ├── draft_meta_info.json            # 草稿元数据
    └── assets/                         # 素材文件
        ├── scene_E1S01.mp4
        ├── scene_E1S02.mp4
        └── ...
```

### 画布配置

根据 ArcReel 项目的 `aspect_ratio.video` 配置决定剪映草稿的画布尺寸：

| ArcReel aspect_ratio | 剪映 canvas_config |
|----------------------|-------------------|
| 16:9（默认横屏） | width=1920, height=1080 |
| 9:16（竖屏） | width=1080, height=1920 |

---

## 后端 API 设计

### 新增端点

#### 1. 签发剪映草稿下载 token

复用现有的下载 token 机制，仅 `purpose` 不同。

```
POST /api/v1/projects/{name}/export/jianying-draft/token
```

请求：需 Bearer JWT 认证
响应：`{ "download_token": "<jwt>", "expires_in": 300 }`

Token payload 增加 `purpose: "jianying_draft_download"` 以区分普通导出。

#### 2. 导出剪映草稿

```
GET /api/v1/projects/{name}/export/jianying-draft?episode={N}&download_token={token}
```

参数：
- `episode`（必填）：集数编号（整数）
- `download_token`（必填）：下载 token

响应：`application/zip` 流式下载

错误码：
- `404`：项目或集数不存在
- `422`：缺少 episode 参数、集数无已完成视频
- `401`：token 过期或无效
- `403`：token 与项目不匹配

### 服务层设计

新增 `server/services/jianying_draft_service.py`：

```python
"""
剪映草稿导出服务

职责：
  1. 从剧本中提取指定集的已完成视频片段
  2. 调用 pyJianYingDraft 生成草稿文件
  3. 将草稿 + 素材打包为 ZIP
"""

import uuid
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import pyJianYingDraft as draft
from pyJianYingDraft import Intro_type, trange

from lib.project_manager import ProjectManager


class JianyingDraftService:
    """剪映草稿导出服务"""

    # 剪映时间单位：微秒
    MICROSECONDS_PER_SECOND = 1_000_000

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    def export_episode_draft(
        self,
        project_name: str,
        episode: int,
    ) -> Path:
        """
        导出指定集的剪映草稿 ZIP

        Args:
            project_name: 项目名称
            episode: 集数编号

        Returns:
            ZIP 文件路径（临时文件，调用方负责清理）

        Raises:
            FileNotFoundError: 项目或剧本不存在
            ValueError: 无可导出的视频片段
        """
        project = self.pm.load_project(project_name)
        project_dir = self.pm.get_project_path(project_name)

        # 1. 定位剧本文件
        script, script_filename = self._find_episode_script(
            project_name, project, episode
        )

        # 2. 提取已完成视频的场景列表
        clips = self._collect_video_clips(script, project_dir)
        if not clips:
            raise ValueError(
                f"第 {episode} 集没有已完成的视频片段，请先生成视频"
            )

        # 3. 确定画布尺寸
        width, height = self._resolve_canvas_size(project)

        # 4. 在临时目录中生成草稿
        draft_name = f"{project.get('title', project_name)}_第{episode}集"
        tmp_dir = tempfile.mkdtemp(prefix="arcreel_jy_")
        tmp_path = Path(tmp_dir)

        draft_dir = tmp_path / draft_name
        assets_dir = draft_dir / "assets"
        assets_dir.mkdir(parents=True)

        # 5. 复制视频文件到 assets/
        local_clips = []
        for clip in clips:
            src = clip["abs_path"]
            dst = assets_dir / src.name
            # 使用硬链接或复制
            try:
                dst.hardlink_to(src)
            except OSError:
                import shutil
                shutil.copy2(src, dst)
            local_clips.append({
                **clip,
                "local_path": str(dst),
            })

        # 6. 调用 pyJianYingDraft 生成草稿
        self._generate_draft(
            draft_dir=draft_dir,
            draft_name=draft_name,
            clips=local_clips,
            width=width,
            height=height,
        )

        # 7. 打包为 ZIP
        zip_path = tmp_path / f"{draft_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in draft_dir.rglob("*"):
                if file.is_file():
                    arcname = f"{draft_name}/{file.relative_to(draft_dir)}"
                    zf.write(file, arcname)

        return zip_path

    def _find_episode_script(self, project_name, project, episode):
        """定位指定集的剧本文件"""
        episodes = project.get("episodes", [])
        ep_entry = next(
            (e for e in episodes if e.get("episode") == episode), None
        )
        if ep_entry is None:
            raise FileNotFoundError(f"第 {episode} 集不存在")

        script_file = ep_entry.get("script_file", "")
        # script_file 格式: "scripts/episode_N.json"
        filename = Path(script_file).name
        script = self.pm.load_script(project_name, filename)
        return script, filename

    def _collect_video_clips(self, script, project_dir):
        """从剧本中提取已完成视频的场景"""
        content_mode = script.get("content_mode", "narration")
        items = script.get(
            "segments" if content_mode == "narration" else "scenes", []
        )
        id_field = "segment_id" if content_mode == "narration" else "scene_id"

        clips = []
        for item in items:
            assets = item.get("generated_assets") or {}
            video_clip = assets.get("video_clip")
            if not video_clip:
                continue

            abs_path = project_dir / video_clip
            if not abs_path.exists():
                continue

            clips.append({
                "id": item.get(id_field, ""),
                "duration_seconds": item.get("duration_seconds", 8),
                "video_clip": video_clip,
                "abs_path": abs_path,
                "transition": item.get("transition_to_next", "cut"),
            })

        return clips

    def _resolve_canvas_size(self, project):
        """根据项目配置确定画布尺寸"""
        aspect = project.get("aspect_ratio", {}).get("video", "16:9")
        if aspect == "9:16":
            return 1080, 1920
        return 1920, 1080  # 默认 16:9

    def _generate_draft(self, draft_dir, draft_name, clips, width, height):
        """
        使用 pyJianYingDraft 生成草稿文件

        pyJianYingDraft 的 DraftFolder 要求操作的目录结构符合
        剪映草稿目录规范。这里直接在 draft_dir 的父目录创建
        DraftFolder，然后在其中创建草稿。
        """
        # pyJianYingDraft 需要一个"草稿根目录"来模拟剪映的目录结构
        # draft_dir 的父目录作为草稿根
        folder = draft.DraftFolder(str(draft_dir.parent))

        # 创建草稿脚本对象
        script = folder.create_draft(
            draft_name,
            width=width,
            height=height,
        )

        # 添加视频轨道
        script.add_track(draft.TrackType.video)

        # 按顺序添加视频片段
        offset_us = 0
        for clip in clips:
            duration_us = clip["duration_seconds"] * self.MICROSECONDS_PER_SECOND

            # 创建视频片段并添加到轨道
            segment = draft.VideoSegment(
                clip["local_path"],
                trange(f"{offset_us}us", f"{duration_us}us"),
            )
            script.add_segment(segment)
            offset_us += duration_us

        # 保存草稿（生成 draft_content.json + draft_meta_info.json）
        script.save()
```

> **注意**：上述代码为设计意图的伪代码示意。实际实现时需根据 pyJianYingDraft 的 API 细节调整（如 `trange` 的参数格式、`DraftFolder` 的目录管理方式等）。实现阶段应先阅读 pyJianYingDraft 的 demo.py 和文档确认 API 用法。

### 路由层

在 `server/routers/projects.py` 中新增两个端点：

```python
@router.post("/{name}/export/jianying-draft/token")
async def create_jianying_draft_token(
    name: str,
    current_user: User = Depends(get_current_user),
):
    """签发剪映草稿下载 token"""
    # 复用 create_download_token，purpose 改为 jianying_draft_download
    ...

@router.get("/{name}/export/jianying-draft")
async def export_jianying_draft(
    name: str,
    episode: int = Query(..., description="集数编号"),
    download_token: str = Query(..., description="下载 token"),
):
    """导出剪映草稿 ZIP"""
    # 1. 验证 download_token
    # 2. 调用 JianyingDraftService.export_episode_draft()
    # 3. 返回 FileResponse（ZIP 流式下载）
    # 4. 清理临时文件（使用 BackgroundTask）
    ...
```

### 认证中间件调整

在 `server/app.py` 的认证中间件中，为 `/api/v1/projects/*/export/jianying-draft` 路径增加与现有导出端点相同的 `download_token` 放行逻辑。

---

## 前端交互设计

### 入口位置

在工作台（workspace）页面的集级操作区域（每集的 header 右侧）添加"导出剪映草稿"按钮，与现有的"生成分镜"、"生成视频"按钮并列。

也可在全局导出弹窗（`ExportScopeDialog`）中新增第三个选项"导出为剪映草稿（按集）"。

### 交互流程

1. 用户点击"导出剪映草稿"按钮
2. 如果项目有多集，显示集数选择（单选下拉）；仅一集则直接进入下一步
3. 前端检查该集是否有已完成视频（通过 `episodes[].videos.completed > 0`），无则提示"请先生成视频"
4. 签发下载 token → 触发浏览器原生下载
5. 下载完成后，显示导入指引 toast/弹窗：
   - "下载完成！请将 ZIP 解压后，将文件夹复制到以下目录："
   - Windows：`C:\Users\{用户}\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft\`
   - macOS：`/Users/{用户}/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`
   - "然后重启剪映即可在草稿箱中看到该项目。"

### 前端 API 层

在 `frontend/src/api.ts` 中新增：

```typescript
/** 签发剪映草稿下载 token */
async requestJianyingDraftExportToken(projectName: string): Promise<{download_token: string}> {
  const res = await this.fetch(`/projects/${projectName}/export/jianying-draft/token`, {
    method: "POST",
  });
  return res.json();
}

/** 构造剪映草稿下载 URL */
getJianyingDraftDownloadUrl(projectName: string, episode: number, downloadToken: string): string {
  return `${this.baseUrl}/projects/${projectName}/export/jianying-draft?episode=${episode}&download_token=${downloadToken}`;
}
```

---

## 风险与缓解措施

### 1. 剪映草稿格式无官方文档

**风险**：所有社区工具（包括 pyJianYingDraft）均基于逆向工程，剪映更新可能导致草稿格式不兼容。

**缓解**：
- pyJianYingDraft 社区活跃，通常在剪映版本更新后数周内跟进适配
- ArcReel 仅使用"创建新草稿"功能（不读取已有草稿），此功能在剪映 5+ 所有版本中均可用
- 在导出功能旁标注"支持剪映 5.0 及以上版本"
- 锁定 pyJianYingDraft 版本号，升级前先在 CI 中验证

### 2. 剪映 6+ 草稿加密

**风险**：剪映 6 及以上版本对 `draft_content.json` 进行了加密。

**缓解**：加密仅影响**读取已有草稿**（模板模式），**创建新草稿**不受影响。ArcReel 的场景是从零生成草稿，因此当前不受此限制。若字节跳动未来对新建草稿也实施加密/签名校验，需关注 pyJianYingDraft 社区的应对方案。

### 3. 导出 ZIP 体积较大

**风险**：每集可能包含数十个视频片段，ZIP 体积可达数百 MB。

**缓解**：
- 使用浏览器原生下载（已有机制），支持进度显示和断点续传
- ZIP 内视频使用 `ZIP_DEFLATED` 压缩（视频本身已压缩，实际压缩比有限，但不会增加体积）
- 后续可考虑只导出草稿 JSON（不含素材），配合桌面 Helper 工具按需下载

### 4. 服务端临时目录清理

**风险**：大量导出请求可能导致临时文件堆积，占满磁盘。

**缓解**：
- 使用 FastAPI 的 `BackgroundTask` 在响应完成后立即清理临时目录
- 对临时目录设置最大存活时间，定期清理超时文件
- 使用硬链接（`hardlink_to`）避免实际复制视频文件（仅在同一文件系统上有效）

### 5. pymediainfo 在 Linux 服务器上的依赖

**风险**：pyJianYingDraft 依赖 `pymediainfo` 来获取视频时长和分辨率，需要系统安装 `mediainfo`。

**缓解**：Docker 镜像中已有 `ffmpeg`（同为媒体处理工具），添加 `mediainfo` 只需一行 `apt-get install`，无额外风险。

---

## 依赖变更

### Python 依赖

在 `pyproject.toml` 的 `dependencies` 中添加：

```toml
"pyjianyingdraft>=0.2.6",
```

### 系统依赖

在 Docker 镜像中添加：

```dockerfile
RUN apt-get update && apt-get install -y mediainfo && rm -rf /var/lib/apt/lists/*
```

---

## 实施任务拆分

### 阶段 1：后端核心逻辑

- [ ] 1.1 添加 `pyjianyingdraft` 依赖到 `pyproject.toml`，Docker 镜像添加 `mediainfo`
- [ ] 1.2 创建 `server/services/jianying_draft_service.py`，实现 `JianyingDraftService` 类
- [ ] 1.3 编写 pyJianYingDraft 集成的单元测试（mock 视频文件，验证生成的 JSON 结构）
- [ ] 1.4 在 `server/routers/projects.py` 中添加下载 token 签发端点和草稿导出端点
- [ ] 1.5 调整认证中间件，放行剪映草稿导出路径的 download_token 认证
- [ ] 1.6 编写路由层集成测试

### 阶段 2：前端交互

- [ ] 2.1 在 `frontend/src/api.ts` 中添加剪映草稿相关 API 方法
- [ ] 2.2 创建 `JianyingDraftExportButton` 组件（含集数选择逻辑）
- [ ] 2.3 在工作台页面集成导出按钮
- [ ] 2.4 实现导入指引弹窗/toast
- [ ] 2.5 编写前端组件测试

### 阶段 3：验证与文档

- [ ] 3.1 端到端手动验证：导出 → 解压 → 剪映导入 → 确认时间线正确
- [ ] 3.2 验证 narration 模式（segments）和 drama 模式（scenes）均正常工作
- [ ] 3.3 在 `docs/getting-started.md` 中补充剪映草稿导出使用说明

---

## 后续扩展（非 MVP 范围）

- **导出音频轨**：将配音/BGM 作为独立音频轨添加到草稿
- **导出字幕轨**：将 `novel_text`（说书模式）或 `dialogue`（剧集模式）作为字幕导入
- **转场效果映射**：将剧本中的 `transition_to_next` 映射为剪映的转场特效
- **CapCut 国际版支持**：使用 pyCapCut 库（同作者开发中）适配国际版草稿格式
- **桌面 Helper 工具**：参考 capcut-mate-electron，开发轻量 Electron 工具实现一键导入
- **仅导出草稿 JSON（无素材）**：草稿 JSON 中嵌入素材下载 URL，配合 Helper 工具按需下载，大幅减小 ZIP 体积