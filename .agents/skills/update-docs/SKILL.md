---
name: update-docs
description: 根据最近的 git 改动，更新面向用户的文档（README 双语、入门教程、部署、剪映导出等）。手动调用。
disable-model-invocation: true
---

按最近的代码改动更新面向用户的文档。两个引擎：引擎 A 读 git 历史，找已落地但文档未反映的新能力；引擎 B 读文档，找与代码现状不符的过时内容。措辞与结构改动直接修改，事实项经用户确认后再改。

## 适用范围

in-scope 文档分两组：

- **引擎 A 组**：高频、主题宽的文档。两个引擎都覆盖，并参与 baseline 计算。
- **仅引擎 B 组**：低频、主题窄的文档。只做事实核对，不参与 baseline。

`website/docs/` 下每个 `.md` / `.mdx` 页面在 frontmatter 用 `update_docs` 声明归属：`engine-a`、`engine-b` 或 `none`（明确不参与）。新增页面必须声明，否则收集脚本与 CI 一致性检查都会失败。`README.md` / `CONTRIBUTING.md` 等非 Docusaurus 根目录文件仍在收集脚本内少量枚举。CONTRIBUTING「各页职责」须登记全部上站源页面，包括声明为 `none` 的页面；CI 双向校验缺页和多页。

README.en.md 是 README.md 的镜像，中文为源：不独立进引擎，改完后随中文做全文一致性核对（第 6 步）。

面向用户的文档源文件在 `website/docs/` 下（发布到 docs.arc-reel.com）。排除供应商费用表，以及未上站的内部文档（`docs/` 下的 adr、research、各供应商 SDK 文档等）。新增上站页面按性质声明 `update_docs`，并同步 CONTRIBUTING「各页职责」。

## 步骤

### 1. 收集

运行 `bash .agents/skills/update-docs/scripts/collect-changes.sh`，得到 baseline、全量候选 commit 标题、引擎 A 覆盖文档清单与引擎 B 文档清单。任一上站页面未声明归属时脚本非零退出，按提示补 frontmatter 与 CONTRIBUTING「各页职责」后重跑。

### 2. 引擎 A：git 历史 → 文档

派一个只读 subagent（`subagent_type: Explore`，提示词 `.agents/skills/update-docs/references/gap-finder.md`），传入仓库根路径、收集输出中的引擎 A 覆盖文档与全量候选 commit 标题清单，产出每篇引擎 A 文档的遗漏能力清单。
完成判据：拿到 subagent 列出的遗漏项。

### 3. 引擎 B：文档 → 代码

对引擎 B 清单中每篇文档派一个只读 subagent 并行核对（`subagent_type: Explore`，提示词 `.agents/skills/update-docs/references/fact-checker.md`）。
完成判据：每篇文档都有事实核对结果。

### 4. 分类

合并两个引擎的待改项：先按「同文档同位置」去重（引擎 A、B 常撞同一项，如供应商列表），再逐项归为「措辞/结构」「事实项」或「新能力」。
完成判据：每项都已去重并归类。

### 5. 修改

- **措辞/结构**：直接改。
- **事实项**：先列给用户（位置、现状、建议、依据）确认后再改。
- **新能力**：判断是否重要到该进正文（核心能力 / 功能特性）；重要的列给用户确认后写入对应小节，不重要的不进 README（版本流水已由 release-please changelog 覆盖）。

中文文案保持简洁准确，不用翻译腔、口语化或非必要比喻。
完成判据：获准改动全部落地。

### 6. 双语核对

README.md 改完后，对 README.md 与 README.en.md 两篇全文逐节核对，以中文为源把英文修平到一致（含改前就存在的存量偏离）。
完成判据：两篇逐节对应一致。

### 7. 摘要

输出改了哪些文档与小节、哪些事实项已确认、哪些新能力已入正文、英文修平了哪些（含存量），以及哪些留待用户处理。不 commit、不 push。

## 事实项

下列改动须经用户确认，不得直接修改：

- 命令与环境变量：安装/启动/部署命令、`.env` 配置项
- 供应商、模型、能力：供应商列表、默认模型、能力参数。属外部数据，只标记疑点，不猜测或填写具体值
- 版本与依赖要求
- 外部链接
