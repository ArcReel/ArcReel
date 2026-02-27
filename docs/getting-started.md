# 完整入门教程

本教程指导你从零开始将小说转换为短视频。

## 你将学到

1. **环境准备** - 安装所需软件和获取 API 密钥
2. **创建项目** - 建立你的第一个视频项目
3. **完整流程** - 从小说到视频的每一步操作
4. **进阶技巧** - 重新生成、调整参数、费用控制

## 预计耗时

- 环境准备：30-60 分钟（仅首次需要）
- 生成一个 1 分钟视频：约 30 分钟

## 费用预估

本工具使用 Google Gemini API 生成图片和视频，会产生 API 调用费用：

| 类型 | 单价 | 说明 |
|------|------|------|
| 图片生成（1K/2K） | $0.134/张 | 人物设计图、分镜图等 |
| 视频生成（1080p 含音频） | $0.40/秒 | 标准模式 |
| 视频生成（1080p 含音频） | $0.15/秒 | Fast 模式（更快更便宜） |

> :bulb: 示例：一个包含 10 个场景（每场景 8 秒）的短视频
> - 图片：10 张分镜 + 3 张人物设计 = $1.75
> - 视频：80 秒 x $0.15（Fast 模式）= $12
> - **总计约 $14**

---

## 第一章：环境准备

本章将帮助你安装所有必需的软件。如果你已经安装过某个软件，可以跳过对应步骤。

### 1.1 安装 Python

本工具的脚本需要它来运行。

**检查是否已安装：**

打开「终端」（macOS）或「命令提示符」（Windows），输入：

```bash
python3 --version
```

如果显示 `Python 3.10.x` 或更高版本，说明已安装，可跳过。

**安装步骤：**

1. 访问 [Python 官网](https://www.python.org/downloads/)
2. 点击「Download Python 3.x.x」按钮下载
3. 运行安装程序，**勾选「Add Python to PATH」**（重要！）
4. 点击「Install Now」完成安装

### 1.2 安装 Claude Code

Claude Code 是 Anthropic 公司开发的命令行 AI 助手，是本工具的核心交互界面。

**macOS / Linux / WSL 用户：**

打开终端，运行：

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**Windows 用户：**

打开 PowerShell，运行：

```powershell
irm https://claude.ai/install.ps1 | iex
```

**验证安装：**

```bash
claude --version
```

### 1.3 安装 ffmpeg

ffmpeg 是一个视频处理工具，用于将多个视频片段拼接成完整视频。

**macOS 用户：**

```bash
# 使用 Homebrew 安装（推荐）
brew install ffmpeg
```

> :bulb: 如果没有安装 Homebrew，请先访问 [Homebrew 官网](https://brew.sh/) 安装。

**Windows 用户：**

1. 访问 [ffmpeg 下载页面](https://ffmpeg.org/download.html)
2. 下载 Windows 版本并解压
3. 将解压后的 `bin` 文件夹路径添加到系统环境变量 PATH 中

**验证安装：**

```bash
ffmpeg -version
```

### 1.4 获取 Gemini API 密钥

Gemini API 是 Google 提供的 AI 服务，用于生成图片和视频。使用前需要获取 API 密钥。

> :moneybag: **付费层级要求**：图片和视频生成功能需要**付费层级**的 API 密钥。免费层级的密钥无法使用这些功能。
>
> :gift: **新用户福利**：新注册用户可获得 **$300 免费赠金**，有效期 90 天，足够生成大量视频内容！

**获取步骤：**

1. 访问 [Google AI Studio](https://aistudio.google.com/apikey)
2. 使用 Google 账号登录
3. **启用付费层级**：点击「Billing」或「升级」，绑定付款方式（新用户会自动获得 $300 赠金）
4. 点击「Create API Key」创建密钥
5. 复制生成的密钥（以 `AIza` 开头的字符串）

> :warning: **重要**：API 密钥是敏感信息，请妥善保管，不要分享给他人或上传到公开仓库。

### 1.5 下载并配置项目

**下载项目：**

```bash
# 克隆项目到本地
git clone https://github.com/ArcReel/ArcReel.git

# 进入项目目录
cd ArcReel
```

> :bulb: 「克隆」是指将远程仓库的代码下载到本地电脑。

**创建虚拟环境并安装依赖：**

```bash
# 创建虚拟环境（一个独立的 Python 运行空间）
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

# 安装项目所需的 Python 包
pip install -r requirements.txt
```

**配置 API 密钥：**

```bash
# 复制环境变量模板
cp .env.example .env
```

用文本编辑器打开 `.env` 文件，找到 `GEMINI_API_KEY=` 这一行，将你的 API 密钥粘贴在等号后面：

```
GEMINI_API_KEY=your-gemini-api-key-here
```

保存文件，环境准备完成！

---

## 第二章：完整流程

本章将带你完成从小说到视频的完整流程。

### 2.1 启动 Claude Code

在项目目录下启动 Claude Code：

```bash
cd ArcReel
claude
```

启动后会看到一个交互式命令行界面，你可以用自然语言与 AI 对话。

### 2.2 运行完整工作流

输入以下命令启动完整工作流：

```
/manga-workflow
```

AI 会引导你完成以下步骤，每一步都会等待你的确认后再继续：

#### 步骤 1：创建项目

AI 会询问：
- 项目名称（如「我的小说」）
- 小说文件位置

你需要将小说文本文件（.txt 格式）放到 `projects/{项目名}/source/` 目录下。

#### 步骤 2：生成分镜剧本

AI 会自动分析小说内容，将其拆分成适合视频的片段，包括：
- 每个片段的画面描述
- 出场人物列表
- 重要道具/场景（线索）

**审核点**：检查剧本结构是否合理，人物和线索是否识别正确。

#### 步骤 3：生成人物设计图

AI 为每个人物生成设计图，用于保持后续场景中角色外观一致。

**审核点**：检查人物形象是否符合小说描述，不满意可要求重新生成。

#### 步骤 4：生成线索设计图

AI 为重要道具和场景元素生成设计图，如信物、特定地点等。

**审核点**：检查线索设计是否符合预期。

#### 步骤 5：生成分镜图片

AI 根据剧本生成每个场景的静态图片，会自动使用人物和线索设计图作为参考。

**审核点**：检查场景构图、人物一致性、氛围是否正确。

#### 步骤 6：生成视频片段

AI 将分镜图片转换为动态视频，每个场景默认 4-8 秒。

**审核点**：预览每个视频片段，不满意可单独重新生成。

#### 步骤 7：合成最终视频

AI 使用 ffmpeg 将所有片段拼接成完整视频，输出到 `projects/{项目名}/output/` 目录。

---

## 第三章：进阶技巧

掌握以下技巧可以帮助你更高效地使用本工具。

### 3.1 单独运行某个步骤

如果只需要执行某个特定步骤，可以使用单独的命令：

| 命令 | 功能 |
|------|------|
| `/generate-characters` | 只生成人物设计图 |
| `/generate-clues` | 只生成线索设计图 |
| `/generate-storyboard` | 只生成分镜图片 |
| `/generate-video` | 只生成视频片段 |
| `/compose-video` | 只合成最终视频 |

### 3.2 重新生成不满意的内容

**重新生成单个人物设计图：**

在 Claude Code 中直接说：

```
请重新生成「姜月茴」的人物设计图
```

**重新生成单个分镜图片：**

```
请重新生成 E1S03 的分镜图
```

**重新生成单个视频片段：**

```
请重新生成 E1S03 的视频
```

### 3.3 使用 Web UI 管理项目

Web UI 提供可视化界面，方便管理项目和预览素材。

**启动 Web UI：**

```bash
# 终端 1：启动后端
uv run uvicorn server.app:app --reload --port 8080

# 终端 2：启动前端
cd frontend
pnpm install
pnpm dev
```

在浏览器中打开 http://localhost:5173

**Web UI 功能：**

- **项目列表**：查看所有项目及其状态
- **素材预览**：浏览人物设计图、分镜图、视频片段
- **费用统计**：查看 API 调用次数和费用明细
- **参数调整**：修改项目配置（如视频时长、画面比例）

### 3.4 控制费用

**查看费用统计：**

- Web UI：访问 http://localhost:8080/app/usage
- 命令行：查看项目目录下的费用日志

**减少重复生成：**

- 仔细审核每个阶段的输出，减少返工
- 先生成少量场景测试效果，满意后再批量生成

### 3.5 断点续传

如果视频生成过程中断（如网络问题），可以从上次位置继续：

```
/generate-video --resume
```

系统会自动跳过已生成的片段，只生成剩余部分。

---

## 第四章：常见问题

### Q: API 调用失败怎么办？

1. **检查 API 密钥**：确认 `.env` 文件中的 `GEMINI_API_KEY` 是否正确
2. **检查网络**：确保可以访问 Google 服务
3. **检查配额**：访问 [Google AI Studio](https://aistudio.google.com/) 查看 API 使用量是否超限

### Q: 人物在不同场景中长得不一样？

1. 确保先运行 `/generate-characters` 生成人物设计图
2. 检查生成的人物设计图是否满意，不满意要先重新生成
3. 系统会自动使用人物设计图作为参考，确保后续场景一致

### Q: 视频生成很慢？

视频生成通常需要 1-3 分钟/片段，这是正常的。影响因素：
- 视频时长（4秒 vs 8秒）
- API 服务器负载
- 网络状况

### Q: 生成中断了怎么办？

使用断点续传功能：

```
/generate-video --resume
```

系统会自动从上次中断的位置继续。

### Q: ffmpeg 未找到？

确保 ffmpeg 已正确安装并添加到系统 PATH：

```bash
ffmpeg -version
```

如果提示找不到命令，请参考 [1.3 安装 ffmpeg](#13-安装-ffmpeg) 重新安装。

### Q: 如何修改视频比例？

默认生成 9:16 竖屏视频。如需修改，在 `project.json` 中设置 `aspect_ratio` 字段，或使用 Web UI 调整。

---

## 下一步

恭喜你完成了入门教程！接下来你可以：

- :book: 阅读 [CLAUDE.md](../CLAUDE.md) 了解更多技术细节
- :moneybag: 查看 [费用说明](视频&图片生成费用表.md) 了解详细定价
- :wrench: 探索 [Veo API 参考](veo.md) 了解视频生成技术

如有问题，欢迎提交 [Issue](https://github.com/your-repo/issues) 反馈！
