# 剪映草稿导出功能设计

**日期**：2026-03-23
**状态**：已确认

---

## 概述

将 ArcReel 单集已生成的视频片段导出为剪映（JianYing）草稿文件，用户解压到本地剪映草稿目录后，在剪映中直接打开进行二次编辑（字幕、转场、特效等）。

### 设计目标

- 按集导出，视频素材在时间线上顺序排列
- 说书模式附带字幕轨（`novel_text`）
- 素材自包含：ZIP 内含草稿 JSON + 视频文件
- 复用现有下载 token + 浏览器原生下载机制
- 用户通过弹窗填写剪映草稿目录，后端生成路径正确的 `draft_content.json`

### 非目标

- 不支持剪映模板模式（读取/修改已有草稿）
- 不支持远程 URL 素材引用
- 不导出音频轨（BGM、配音）
- 不支持 CapCut 国际版
- drama 模式不导出字幕（多角色对话结构复杂，非 MVP）

---

## 技术选型

**pyJianYingDraft**（`pyjianyingdraft>=0.2.6`）：2800+ Star 的成熟社区库，API 简洁，pip 可安装，与 ArcReel Python 后端一致。系统依赖 `mediainfo`（Docker 中需 `apt-get install`）。

---

## 后端 API 设计

### 端点

#### 1. 签发 token

```
POST /api/v1/projects/{name}/export/jianying-draft/token
```

- 需 Bearer JWT 认证
- 复用 `create_download_token`，purpose 保持 `"download"`
- 响应：`{ "download_token": "...", "expires_in": 300 }`

#### 2. 导出草稿 ZIP

```
GET /api/v1/projects/{name}/export/jianying-draft
    ?episode={N}
    &draft_path={用户本地剪映草稿根目录}
    &download_token={token}
```

- `episode`（必填）：集数编号
- `draft_path`（必填）：用户本地剪映草稿根目录的绝对路径
- `download_token`（必填）：下载 token
- 响应：`application/zip` 流式下载

错误码：

| 状态码 | 场景 |
|--------|------|
| 404 | 项目或集数不存在 |
| 422 | 该集无已完成视频 / draft_path 为空 |
| 401 | token 过期或无效 |
| 403 | token 与项目不匹配 |

认证中间件：为 `/api/v1/projects/*/export/jianying-draft` 增加与现有导出端点相同的 `download_token` 放行逻辑。

---

## 服务层设计

新增 `server/services/jianying_draft_service.py`：

```python
class JianyingDraftService:
    def export_episode_draft(
        self, project_name: str, episode: int, draft_path: str
    ) -> Path:
```

### 核心流程

1. **加载剧本**：区分 `content_mode`（narration → segments，drama → scenes）
2. **收集已完成视频**：遍历 `generated_assets.video_clip`，仅保留文件存在的片段；narration 模式额外提取 `novel_text`
3. **确定画布尺寸**：`aspect_ratio.video` → 16:9 = 1920×1080，9:16 = 1080×1920
4. **创建临时目录**，复制视频到 `assets/`（优先硬链接）
5. **调用 pyjianyingdraft 生成草稿**：
   - `DraftFolder(tmp_dir)` → `create_draft(draft_name, width, height)`
   - `add_track(TrackType.video)` — 视频轨
   - 逐片段用 `VideoMaterial(path).duration` 预读实际时长，构造 `VideoSegment`
   - narration 模式：`add_track(TrackType.text, "字幕")` + 逐段 `TextSegment`
6. **路径后处理**：`save()` 后读取 `draft_content.json`，将临时目录路径全文替换为 `{draft_path}/{draft_name}/assets/...`
7. **打包 ZIP**，`BackgroundTask` 清理临时文件

### 视频时长策略

忽略剧本中的 `duration_seconds`，使用 pyjianyingdraft 从视频文件自动提取的实际时长。避免时长不匹配导致的 `ValueError`。

### 字幕轨（仅 narration 模式）

```python
if content_mode == "narration":
    script.add_track(draft.TrackType.text, "字幕")
    text_style = draft.TextStyle(
        size=8.0, color=(1.0, 1.0, 1.0), align=1,
        bold=True, auto_wrapping=True,  # 导出为 subtitle 类型
    )
    for clip in clips:
        if clip.get("novel_text"):
            seg = draft.TextSegment(
                text=clip["novel_text"],
                timerange=trange(offset_us, clip["actual_duration_us"]),
                style=text_style,
            )
            script.add_segment(seg)
```

字幕时长与对应视频片段实际时长一致。无 `novel_text` 的片段跳过字幕。

---

## 前端交互设计

### 入口

在现有 `ExportScopeDialog` 弹窗中新增第三个选项：**"导出为剪映草稿"**。

选择后展开额外表单：

#### 1. 集数选择（下拉框）

- 数据源：`project.episodes[]`
- 仅列出有已完成视频的集
- 仅一集时自动选中，不显示下拉

#### 2. 剪映草稿目录（文本输入框）

- OS 检测预填默认值：
  - Windows: `C:\Users\YOUR_USERNAME\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft`
  - macOS: `/Users/YOUR_USERNAME/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`
- 输入框下方提示：*"请填入剪映草稿目录的完整路径。打开剪映 → 设置 → 草稿位置 可查看。"*
- `localStorage` key `arcreel_jianying_draft_path` 缓存，有缓存时优先回填

#### 3. 导出按钮

- 点击：签发 token → `window.open(GET url)` 触发浏览器下载
- 下载期间按钮禁用，显示"导出中..."

### API 层

在 `frontend/src/api.ts` 新增：

```typescript
requestJianyingDraftExportToken(projectName: string): Promise<{ download_token: string }>
getJianyingDraftDownloadUrl(projectName: string, episode: number, draftPath: string, token: string): string
```

---

## 导出 ZIP 包结构

```
{项目名}_第{N}集_剪映草稿.zip
└── {项目名}_第{N}集/
    ├── draft_content.json      # 路径已替换为用户本地路径
    ├── draft_meta_info.json
    └── assets/
        ├── scene_E1S01.mp4
        ├── scene_E1S02.mp4
        └── ...
```

### 用户使用流程

1. 工作台 → "导出 ZIP" → 选 "导出为剪映草稿"
2. 选集数 + 填写剪映草稿目录（localStorage 自动回填）
3. 点击导出，浏览器下载 ZIP
4. 将 ZIP 解压到填写的剪映草稿目录中
5. 打开剪映，草稿列表出现该项目，时间线已排好视频 + 字幕

---

## 路径后处理

`save()` 后全文替换 `draft_content.json` 中的临时目录路径：

```python
content = json_path.read_text(encoding="utf-8")
content = content.replace(
    str(tmp_assets_dir),
    f"{draft_path}/{draft_name}/assets"
)
json_path.write_text(content, encoding="utf-8")
```

所有素材路径前缀统一在 `assets/` 下，单次替换即可。

---

## 错误处理

| 场景 | 处理 |
|------|------|
| 集数不存在 | 404 + "第 N 集不存在" |
| 该集无已完成视频 | 422 + "请先生成视频" |
| 视频文件缺失（script 有记录但文件不在） | 跳过该片段，仅导出存在的视频 |
| pyjianyingdraft 生成失败 | 500 + 日志记录，返回友好错误 |
| draft_path 为空或格式异常 | 422 + "请提供有效的剪映草稿目录路径" |

---

## 依赖变更

### Python

```toml
# pyproject.toml
"pyjianyingdraft>=0.2.6",
```

### 系统

```dockerfile
RUN apt-get update && apt-get install -y mediainfo && rm -rf /var/lib/apt/lists/*
```

---

## 测试策略

- **单元测试**：mock 视频文件（用 `imageio` 生成短视频），验证 `draft_content.json` 结构正确、路径替换正确、字幕轨存在（narration 模式）
- **路由集成测试**：复用现有 `test_projects_archive_routes.py` 模式，测试 token 签发 + ZIP 下载 + 错误码

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 剪映格式无官方文档，更新可能不兼容 | pyJianYingDraft 社区活跃，通常数周内跟进；锁定版本号 |
| 剪映 6+ 草稿加密 | 仅影响读取已有草稿，创建新草稿不受影响 |
| ZIP 体积大（数十个视频片段） | 浏览器原生下载支持进度显示；硬链接避免实际复制 |
| 临时目录堆积 | `BackgroundTask` 响应完成后立即清理 |
| pymediainfo 需系统 mediainfo | Docker 中一行 apt-get 即可 |

---

## 后续扩展（非 MVP）

- 导出音频轨（BGM、配音）
- drama 模式字幕（多角色对话）
- 转场效果映射（`transition_to_next` → 剪映转场）
- CapCut 国际版支持
- 桌面 Helper 工具（一键导入）
