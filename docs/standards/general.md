---
paths:
  - "**"
---

# 通用

## 代码与文档

### 注释只陈述代码现在的行为与约束

读注释的人手里只有这份代码。变更原因、issue 与 PR 编号、「最近」「本次」「实测」这类时间性措辞属于 commit message 与 PR 描述；写进注释后，代码一改它就变成错误陈述，而且没有人会去更新它。修改文件时，顺手把已有的这类引用改写成对当前行为的陈述。

放行条件：这条规则约束代码与测试中的注释；`docs/` 下的专题文档（ADR、runbook 等）之间可以互相引用 spec 编号。

### 工具报出的问题改代码；豁免只给确认过的工具误报

lint、类型检查、依赖检查报错时修改代码，不加 baseline、计数阈值或整条规则关闭。豁免只用于确认过的误报。行内豁免是否在同一行写了理由由 `scripts/audit_conventions.py` 的 `SUPPRESSION-REASON` 检查，review 只判断理由是否成立。

行内语法不可用时，豁免写在配置条目旁，每个条目旁用一行注释写明理由：

- actionlint：写在 `.github/actionlint.yaml` 的 `paths.<文件>.ignore`，按错误消息正则精确匹配。
- deptry 的 `DEP002` / `DEP004`：写在 `[tool.deptry.per_rule_ignores]`，按包名逐项列出。
- ESLint 的文件级关闭：经 `eslint.config.js` 的 `files` override。

策略阈值类的发现（如 Dependabot 冷却期天数）按工具要求调整配置，同样不走豁免。

理由要说明「为什么这里工具判错了」或「为什么这里只能这样写」。「太麻烦」「暂时这样」「later fix」不成立；「React setter 引用稳定」「mount-only 初始化」「第三方库未提供类型」成立。

### 功能变更与对应文档在同一个 PR 里

下列变更同步修改 `website/docs/` 中的对应页面（各页职责见 `CONTRIBUTING.md`「各页职责」）。文档落后于代码的那段时间里，用户按文档操作会失败。

- 新增创作类型或生成模式。
- 新增供应商或媒体能力。
- 部署目录、端口、环境变量变化。
- 数据目录、备份方式、迁移行为变化。
- 对外 API、许可证或商业使用方式变化。
- 外部 Agent 的安装方式变化：同步 `public/agent-installation-guide.md`。

## 文案

### 用户可见文本写成翻译 key，覆盖全部已支持语言

前端界面文本写进 `frontend/src/i18n/`，后端返回给用户的错误、事件等消息写进 `lib/i18n/`，各自的语言目录就是语言清单。各语言 key 集合是否一致由 typecheck 与 `tests/unit/lib/i18n/` 校验。编写或修改中文界面文案（`frontend/src/i18n/zh`、`lib/i18n/zh`）与用户文档（`website/docs`）前，用 Skill 工具调用 `tech-doc-style-chinese`。中文文案先交维护者校对；其他语言先补齐 key 以通过校验，中文定稿后再更新译文。
