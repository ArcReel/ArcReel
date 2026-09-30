# Chinese Tech Doc Style

本项目只是一份面向中文技术文档、产品文案与界面文案的写作 Skill。

这份 Skill 的目标很明确：中文技术写作应更克制、更准确、更易读。不追求宣传感，也不试图把所有内容都写成统一模板，而是聚焦几类高频问题：

- 中文技术文案容易空泛、重复、宣传化
- 中文与英文、数字混合排版时可读性差
- 常见英文状态词和错误词容易被机械直译
- 文档首页、解决方案页、接口说明页、FAQ 的信息密度和结构经常失衡

如果需要一套适合中文技术文档的基础写作规范，这份 Skill 可以直接拿来使用，或是作为参考。

## 适用场景

本 Skill 适合以下内容：

- 文档首页、落地页、首屏文案
- 接口文档、参数说明、错误码说明、更新日志
- 产品能力介绍、解决方案页、能力说明页
- 界面文案、按钮文案、导航标签、提示信息

不适合以下内容：

- 代码字面量
- JSON 键名
- URL
- API 路径
- 数据库字段名
- 其他机器可读标识符

## 核心规则概览

这份 Skill 主要覆盖以下规则：

- 改写时保留事实、限制、条件和确定程度
- 中文引号统一使用直角引号 `「」`
- 默认避免不必要的直接称呼，允许项目语气覆盖
- 在可见正文中处理中文与英文、数字之间的留白
- 中文段落在源文件中保持一段一行，不做硬换行
- 避免机械直译 `Success`、`Invalid`、`Bad Request` 等英文状态词
- 避免高频互联网黑话，如 `赋能`、`抓手`、`闭环`、`打通`
- 对操作、排查和运维文档应用受控中文技术写作方法

完整规范请阅读：

- [SKILL.md](./SKILL.md)
- [公开说明稿](./NoCode-Skill.md)

## 仓库结构

```text
tech-doc-style-chinese/
├── SKILL.md
├── NoCode-Skill.md
├── README.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── api-status-copy.md
│   ├── controlled-technical-chinese.md
│   ├── project-overrides-example.md
│   └── terminology-and-typography.md
├── scripts/
│   ├── lint_copy_rules.py
│   └── unwrap_md_paragraphs.py
└── tests/
    ├── test_lint_copy_rules.py
    ├── test_skill_structure.py
    └── test_unwrap_md_paragraphs.py
```

各文件的作用：

- `SKILL.md`：正式技能入口，供 Codex、Claude Code 等 Agent 使用
- `NoCode-Skill.md`：对外说明稿，适合公开阅读和分享
- `README.md`：GitHub 仓库首页说明
- `agents/openai.yaml`：技能展示元数据
- `references/`：按任务读取的详细规则和项目覆盖模板
- `scripts/lint_copy_rules.py`：轻量检查器
- `scripts/unwrap_md_paragraphs.py`：展开段落硬换行的工具
- `tests/`：检查器、技能结构和硬换行工具的回归测试

## 如何在 Codex 中使用

### 使用 npx 安装（推荐）

如果本机有 Node.js 环境，可直接用 `npx skills` 安装：

```bash
# 直接安装
npx skills add https://github.com/Fenng/tech-doc-style-chinese
```

如需无交互并明确安装到全局 Codex，可使用：

```bash
npx -y skills add https://github.com/Fenng/tech-doc-style-chinese -a codex -g
```

参数说明：

- `-a codex` 表示安装到 Codex agent
- `-g` 表示全局安装（用户级），不加则安装到当前项目范围
- `-y` 表示跳过交互确认，便于自动化执行

安装后建议重启 Codex，以确保新 Skill 被加载。

### 按 Release 安装（推荐）

固定版本安装，便于团队复现：

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_HOME/skills"

git clone --depth 1 --branch <release-tag> \
  https://github.com/Fenng/tech-doc-style-chinese.git \
  "$CODEX_HOME/skills/tech-doc-style-chinese"
```

`<release-tag>` 可替换为已发布版本，例如 `v0.1.0.2.4`。

### 本地目录安装（开发场景）

如果正在本地修改或调试，可直接复制目录：

```bash
mkdir -p "$CODEX_HOME/skills/tech-doc-style-chinese"
cp -R ./* "$CODEX_HOME/skills/tech-doc-style-chinese/"
```

安装后可快速校验：

```bash
test -f "$CODEX_HOME/skills/tech-doc-style-chinese/SKILL.md" && echo "installed"
```

安装完成后，可在任务中显式调用：

```text
Use $tech-doc-style-chinese to rewrite this Chinese technical copy.
```

或者直接在相关任务中触发，例如：

- 重写中文技术文案
- 整理 FAQ
- 优化 API 文档措辞
- 优化落地页中文文案

## 如何在 Claude Code 中使用

### 直接让 Claude Code 安装（最简单）

如果当前 Claude Code 环境支持安装 Skills，可让它读取本仓库并安装：

```text
请安装这份 Skill：https://github.com/Fenng/tech-doc-style-chinese
```

这种方式较省事，但具体装到项目级还是全局取决于 Claude Code 当时的能力与判断。团队协作或需要写进文档、CI 的场景，建议用下面的 npx 命令。

### 使用 npx 安装（推荐）

如果本机有 Node.js 环境，可直接用 `npx skills` 安装：

```bash
# 安装到当前项目
npx skills add https://github.com/Fenng/tech-doc-style-chinese -a claude-code
```

如需无交互并明确安装到全局 Claude Code，可使用：

```bash
npx -y skills add https://github.com/Fenng/tech-doc-style-chinese -a claude-code -g
```

参数说明：

- `-a claude-code` 表示安装到 Claude Code
- `-g` 表示全局安装（用户级，写入 `~/.claude/skills/`），不加则安装到当前项目范围（写入 `./.claude/skills/`）
- `-y` 表示跳过交互确认，便于自动化执行

安装后建议重启 Claude Code，以确保新 Skill 被加载。

### 本地目录安装（开发场景）

如果正在本地修改或调试，可直接复制目录：

```bash
mkdir -p ~/.claude/skills/tech-doc-style-chinese
cp SKILL.md ~/.claude/skills/tech-doc-style-chinese/
cp -R references ~/.claude/skills/tech-doc-style-chinese/
```

安装后可快速校验：

```bash
test -f ~/.claude/skills/tech-doc-style-chinese/SKILL.md && echo "installed"
```

Claude Code 会根据 `SKILL.md` 里的 `description` 自动判断何时调用该 Skill，无须手动触发，例如：

- 重写中文技术文案
- 整理 FAQ
- 优化 API 文档措辞
- 优化落地页中文文案

## 如何做项目级覆盖

这份 Skill 只放通用规则，不把某个项目的版本展示、品牌语气、术语表或信息架构硬编码到核心规范里。

如果项目存在自己的约定，在目标项目中建立单独的覆盖文件。可以从以下模板开始：

- `references/project-overrides-example.md`

这类覆盖文件适合放：

- 版本展示约定
- 品牌或术语偏好
- 文档结构偏好
- 当前项目特有示例

模板本身不包含默认生效的业务术语。不要把示例文件当成目标项目约定。

## 轻量校验与 CI

仓库内置了一个零依赖校验脚本，用于检查高频规则。结果分为：

- `error`：高度确定的错误，默认导致非零退出
- `warning`：依赖语境的可疑表达，需要人工判断
- `style`：项目风格和术语偏好

检查器会保护代码块、行内代码、URL、Markdown 链接目标和单段或多段 API 路径。`截止日期`、`登陆月球`、`配制溶液`、`H5` 等语境项不再作为确定错误。

本地执行：

```bash
python scripts/lint_copy_rules.py
```

仅检查指定文件或目录：

```bash
python scripts/lint_copy_rules.py SKILL.md NoCode-Skill.md references/
```

将警告和风格提示也作为失败处理：

```bash
python scripts/lint_copy_rules.py --strict SKILL.md references/
```

忽略单行检查：

```markdown
需要保留的原文 <!-- copy-lint-disable-line -->
```

运行回归测试：

```bash
python -m unittest discover -s tests -v
```

GitHub Actions 配置文件为 `.github/workflows/skill-lint.yml`，会在 `pull_request` 和 `main` 分支 `push` 时自动运行。

## 展开段落硬换行

为了控制源码行宽而在段落中间手动断行，会让源文件读起来很碎；中文与英文边界上的断行还会在部分渲染器里多出或吞掉空格。仓库提供一个零依赖脚本，把段落和列表项还原为「一段一行」，正文靠编辑器软换行阅读。

检查是否存在硬换行，不改动文件：

```bash
python scripts/unwrap_md_paragraphs.py --check .
```

展开指定文件或目录，直接写回：

```bash
python scripts/unwrap_md_paragraphs.py docs/ README.md
```

只查看结果，不写回文件：

```bash
python scripts/unwrap_md_paragraphs.py --stdout docs/guide.md
```

拼接边界按中西文留白规则处理：中文与中文之间不加空格，中文与半角英文、数字之间加一个空格，全角标点两侧不加空格。

脚本先扫描结构再拼接段落，因此以下内容按整块保留，不只保护起始行：front matter、围栏代码块（包括写在列表标记之后的 `- ```sh`）、缩进代码块（包括列表项内的缩进代码）、表格行、标题、分隔线、多行 HTML 块（`<pre>`、`<script>`、`<style>`、`<textarea>` 和注释）、链接引用定义、引用块，以及行尾两个空格或反斜杠表示的显式换行。

需要整份文件跳过时，在代码块之外单独用一行写出标记：

```markdown
<!-- unwrap-disable-file -->
```

标记只在独立成行且不在代码块、HTML 块内时生效，因此上面这段代码示例不会让本文件免检。

脚本只处理拼接边界，不修改段落内部已有的留白、标点和措辞。展开后仍需人工复查一遍边界留白和列表、表格结构。

## 发布建议

如果只是公开分享规范内容：

- 保留 `NoCode-Skill.md`
- 用 `README.md` 做仓库首页说明

如果希望别人能直接安装使用：

- 保留 `SKILL.md`
- 保留 `agents/openai.yaml`
- 在仓库里明确目录结构和安装方式

<!-- 作者：Fenng（GitHub：@Fenng） -->

## License

本项目采用 MIT License。  
详见 [LICENSE](./LICENSE)。
