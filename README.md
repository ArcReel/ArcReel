# AI 视频生成器

让 Claude Code 帮你把小说自动变成短视频。

## 这是什么？

通过 **Claude Code**（命令行 AI Agent）进行交互式对话，AI 会一步步引导你完成视频制作：
- 拆分小说成适合视频的片段
- 设计人物形象
- 绘制分镜图片
- 生成动态视频
- 拼接成完整作品

基于 **Skills** 和 **Subagent** 实现，每个环节都有专门的 AI 负责处理。

## 两种使用方式

| 方式 | 适合场景 | 说明 |
|------|---------|------|
| **Claude Code** | 完整视频生成流程 | 交互式对话，AI 引导你完成每一步 |
| **Web UI 界面** | 项目管理与进阶操作 | 可视化管理项目、调整参数、预览素材 |

默认输出 9:16 竖屏视频，适合发布到短视频平台。

> :mortar_board: **新手？** 请查看 [完整入门教程](docs/getting-started.md)，手把手教你从零开始。

## 功能特点

- :clapper: **完整工作流**：小说 -> 分镜剧本 -> 人物设计 -> 分镜图片 -> 视频片段 -> 最终视频
- :art: **人物一致性**：AI 先生成人物设计图，后续所有场景都参考该设计，确保角色外观统一
- :key: **线索追踪**：重要道具和场景元素（如信物、特定地点）可标记为"线索"，确保跨场景一致
- :white_check_mark: **人工审核点**：每个阶段都可以暂停审核，不满意可重新生成
- :moneybag: **费用统计**：自动记录 API 调用次数和费用，方便控制成本
- :iphone: **竖屏优化**：默认 9:16 比例，直接发布到短视频平台
- :robot: **AI 驱动**：基于 Claude Code Skills 和 Subagent 架构，专业 AI 处理每个环节
- :desktop_computer: **可视化管理**：Web UI 界面管理项目、预览素材、调整参数

## 安装

### 前置要求

在开始之前，请确保你的电脑已安装：

- **Python 3.10+** - 运行脚本所需（[下载地址](https://www.python.org/downloads/)）
- **uv** - Python 包与环境管理工具（[安装文档](https://docs.astral.sh/uv/)）
- **Node.js 18+** - 运行 React 前端开发服务（[下载地址](https://nodejs.org/)）
- **Claude Code** - 命令行 AI 助手（[使用指南](https://docs.anthropic.com/claude-code)）
- **Anthropic API 密钥** - 用于 Claude Agent SDK（设置 `ANTHROPIC_API_KEY`）
- **ffmpeg** - 视频处理工具（[下载地址](https://ffmpeg.org/download.html)）
- **Gemini API 密钥** - 用于图片和视频生成（[获取地址](https://aistudio.google.com/apikey)）
  > ⚠️ **重要**：需要付费层级的 API 密钥才能使用图片和视频生成功能。新用户注册后可获得 **$300 免费赠金**，足够生成大量视频内容。

> :bulb: 不知道怎么安装这些？请查看 [完整入门教程](docs/getting-started.md) 中的详细步骤。

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/Pollo3470/cc-novel2video.git
cd cc-novel2video

# 2. 安装依赖（uv 会自动创建和管理虚拟环境）
uv sync

# 3. 安装前端依赖
cd frontend
npm install
cd ..

# 4. 配置 API 密钥
cp .env.example .env
# 编辑 .env 文件，填入你的 GEMINI_API_KEY 和 ANTHROPIC_API_KEY
```

## 快速开始

### 方式一：命令行（完整视频生成流程）

```bash
# 1. 进入项目目录
cd cc-novel2video

# 2. 启动 Claude Code
claude

# 3. 运行完整工作流
/manga-workflow
```

AI 会引导你完成以下步骤：
1. 创建项目并上传小说
2. 生成分镜剧本
3. 生成人物设计图
4. 生成线索设计图（重要道具/场景）
5. 生成分镜图片
6. 生成视频片段
7. 合成最终视频

每一步都有审核点，确认满意后再继续下一步。

### 方式二：Web UI（项目管理与进阶操作）

```bash
# 终端 1：启动后端 API
uv run uvicorn webui.server.app:app --reload --port 8080

# 终端 2：启动前端开发服务
cd frontend
npm run dev

# 在浏览器中打开
# http://localhost:5173
```

如果要让后端直接托管前端静态文件：

```bash
cd frontend
npm run build
cd ..
uv run uvicorn webui.server.app:app --reload --port 8080
# 然后访问 http://localhost:8080
```

Web UI 支持：
- 项目列表与项目工作台（`/app/projects`、`/app/projects/{name}`）
- 素材预览（人物图、分镜图、视频片段）
- 参数调整
- 费用统计查看（`/app/usage`）
- 助手会话工作台（`/app/assistant`）
  - 支持输入 `/` 查看 Skills 提示
  - 支持 `/技能名 任务描述` 指定优先使用的 Skill
  - 支持通过 `ASSISTANT_ANTHROPIC_BASE_URL` 自定义 Claude API Base URL

## 项目结构

```
cc-novel2video/
├── .claude/
│   ├── agents/           # Subagent（子代理）：处理复杂多步骤任务
│   │   ├── novel-to-narration-script.md   # 小说 → 说书剧本
│   │   └── novel-to-storyboard-script.md  # 小说 → 分镜剧本
│   └── skills/           # Skills（技能模块）：处理单一任务
│       ├── generate-characters/   # 生成人物设计图
│       ├── generate-clues/        # 生成线索设计图
│       ├── generate-storyboard/   # 生成分镜图片
│       ├── generate-video/        # 生成视频片段
│       ├── compose-video/         # 合成最终视频
│       └── manga-workflow/        # 主流程编排
├── lib/                  # Python 共享库
├── projects/             # 你的视频项目存放处
├── webui/
│   └── server/           # FastAPI 后端 API 服务
├── frontend/             # React + Vite 前端工程
│   ├── src/              # 前端源码
│   ├── package.json      # 前端依赖与脚本
│   └── dist/             # 前端构建产物（可由后端托管）
├── .env.example          # 环境变量模板
├── CLAUDE.md             # Claude 系统配置
├── pyproject.toml        # Python 依赖（uv 主配置）
└── requirements.txt      # 过渡依赖清单（可选）
```

### 视频项目目录

每个项目存放在 `projects/{项目名}/` 下：

```
projects/我的小说/
├── source/       # 原始小说（.txt 文件）
├── scripts/      # 分镜剧本（.json 文件）
├── characters/   # 人物设计图（.png）
├── clues/        # 线索设计图（.png）
├── storyboards/  # 分镜图片（.png）
├── videos/       # 视频片段（.mp4）
└── output/       # 最终输出（.mp4）
```

## 可用命令

在 Claude Code 中输入以下命令：

| 命令 | 功能 |
|------|------|
| `/manga-workflow` | 完整工作流程（推荐新手使用） |
| `/generate-characters` | 生成人物设计图 |
| `/generate-clues` | 生成线索设计图 |
| `/generate-storyboard` | 生成分镜图片 |
| `/generate-video` | 生成视频片段 |
| `/compose-video` | 合成最终视频（添加转场、BGM） |

## 详细文档

- :book: [完整入门教程](docs/getting-started.md) - 从零开始的手把手指南
- :moneybag: [费用说明](docs/视频&图片生成费用表.md) - API 调用费用参考
- :wrench: [Veo API 参考](docs/veo.md) - 视频生成技术细节

## 许可证

[AGPL-3.0](LICENSE)
