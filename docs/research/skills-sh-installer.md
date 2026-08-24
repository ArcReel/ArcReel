# skills.sh 安装器包格式与跨客户端能力调研

**调研截止日期**：2026-08-24
**用途**：#2060 的调研产出，作为 Spec #1711（承接 #1709 三层分发架构）的事实基线
**调研对象**：Vercel 的 skills.sh 生态——`npx skills` CLI（GitHub: vercel-labs/skills，npm 包 `skills`，v1.5.23）、skills.sh 目录站、agentskills.io 开放规范，以及 Claude Code / Codex / Cursor 三个目标客户端的原生 skill 支持

---

## 0. 调研范围与方法

逐点核实 #2060 列出的五个问题，全部结论溯源到一手来源：CLI 源码（`vercel-labs/skills` 仓库 `src/`）、官方 README、skills.sh 官网文档、agentskills.io 规范全文、各客户端官方文档。每节末尾附来源 URL。

不在范围：ArcReel 侧 skill 包的具体内容设计（属 #1711）。

---

## 1. 发布与安装

**结论：无中心 registry、无提交流程；任何 git 仓库（含私有，走用户侧 git 凭证）都可作为安装源，`npx skills add <ref>` 支持丰富的 ref 形式，可直指仓库内单个 skill。skills.sh 目录站的收录靠安装遥测自动完成。**

### 1.1 `<ref>` 支持的形式（README「Source Formats」+ `src/source-parser.ts`）

- GitHub 简写 `owner/repo`；扩展形式 `owner/repo/path/to/skill`（子目录）与 `owner/repo@skill-name`（按 skill 名过滤，source-parser.ts:433-442）
- 完整 GitHub URL，含 `https://github.com/owner/repo/tree/main/skills/<name>` 直指子目录；tree ref / `#ref` 语法指定分支或标签，记入 lockfile 的 `ref` 字段
- GitLab URL、`github:`/`gitlab:` 前缀、任意 git URL（`git@host:owner/repo.git`、`ssh://git@host/...`）
- 本地路径（`./my-local-skills`）
- 直链下载 URL：单个合法 SKILL.md，或 `.zip/.tar/.tar.gz/.tgz` 归档；默认限额下载 10 MiB、解压 25 MiB、1000 个文件（`SKILLS_DOWNLOAD_MAX_BYTES` 等环境变量可放宽）——"服务端下发 zip"是 CLI 原生支持的安装通道
- 自托管 well-known 端点：`https://host/.well-known/skills/` 的 index.json（v2 条目 `{name, type: 'skill-md'|'archive', description, url, digest}`，`src/providers/wellknown.ts`）；另有 mintlify、huggingface 等 provider

**无中心 registry**：`<ref>` 全部指向 git 仓库/URL/本地路径，skills.sh 网站只是目录索引，不是安装源。

### 1.2 仓库布局要求（README + `src/skills.ts` + agentskills.io 规范）

- skill = 含 `SKILL.md` 的目录；frontmatter 必填 `name`（小写字母/数字/连字符，规范要求与目录名一致）与 `description`，缺一跳过并告警
- 发现位置：仓库根目录、`skills/`、`skills/.curated|.experimental|.system/`、60 余个 agent 目录（`.claude/skills/`、`.agents/skills/` 等）；容器目录内最多向下 3 层，浅层 SKILL.md 会 shadow 更深层，`--full-depth` 可放开。skill 放仓库深处（如 `server/xxx/skills/`）默认发现不到——要么放浅层，要么用直指子目录的 ref
- 存在 `.claude-plugin/marketplace.json` 或 `plugin.json` 时，其声明的 skills 也被发现
- 一个仓库可含多个 skill：默认交互多选（模糊搜索、空格勾选）；`--skill <name>` 精确指定（`*` 通配全装）、`--list` 只列不装、`--all` = `--skill '*' --agent '*' -y`
- 可选 `metadata.internal: true` 将 skill 隐藏出常规发现（仅 `INSTALL_INTERNAL_SKILLS=1` 时可装）

### 1.3 公开 vs 私有

- **不要求公开仓库**：公私仓库同一命令。GitHub HTTPS/简写按序尝试普通 Git 凭证 → `gh repo clone` → SSH（不执行 `gh auth token`）；GitHub API 访问可显式设 `GITHUB_TOKEN`/`GH_TOKEN`
- 公开性只影响遥测上报与 skills.sh 收录：仓库标识只对确认公开的 GitHub 仓库上报
- skills.sh 目录站无提交流程：`npx skills add` 时匿名遥测（`https://add-skill.vercel.sh/t`）自动收录并按安装量排名；`DISABLE_TELEMETRY=1` / `DO_NOT_TRACK=1` 可关闭。网站有 Packs、Topics、Official 分区

来源：
- https://github.com/vercel-labs/skills （README：Source Formats、Supported Agents、Authentication）
- https://github.com/vercel-labs/skills/blob/main/src/source-parser.ts 、`src/skills.ts` 、`src/providers/wellknown.ts` 、`src/telemetry.ts`
- https://www.skills.sh/docs （目录收录机制）
- https://agentskills.io/specification （SKILL.md 布局规范）

---

## 2. 多文件支持

**结论：完整支持。安装是整个 skill 目录的递归复制，references/、scripts/、assets/ 全部保留、可执行位保留；相对路径引用是 agentskills.io 规范明文定义的机制，三个目标客户端都实现按需读取（progressive disclosure）。**

- `src/installer.ts` 的 `copyDirectory()` 递归复制**整个 skill 目录**，排除项仅：文件 `metadata.json`；目录 `.git`、`__pycache__`、`__pypackages__`。symlink 一律解引用为实体文件（坏 symlink 跳过并警告），保留可执行位，`scripts/` 装完仍可执行
- 默认 **symlink 模式**：先完整复制到 canonical 位置（项目级 `./.agents/skills/<name>/`、全局 `~/.agents/skills/<name>/`），再从各 agent 的 skills 目录建相对 symlink 指回；symlink 失败自动回退 copy，`--copy` 强制各 agent 独立副本。SKILL.md 与附属文件始终同一目录树，相对路径读取无问题
- **唯一改写内容的例外是 Eve**：装到 Eve 时 frontmatter 被裁剪只留 `description`、`license` 与 `metadata` 字符串项；其余客户端原样复制
- agentskills.io 规范定义 `scripts/`、`references/`、`assets/` 约定目录与「use relative paths from the skill root」的引用方式，并给出三级 progressive disclosure 模型（metadata ~100 tokens → SKILL.md 正文 <5000 tokens → 资源文件按需）

来源：
- https://github.com/vercel-labs/skills/blob/main/src/installer.ts
- https://agentskills.io/specification （Optional directories、File references、Progressive disclosure）

---

## 3. 跨客户端

**结论：CLI 支持 77 个 agent；对所有客户端安装产物都是原生 skill 目录——无 AGENTS.md 注入、无 .cursor/rules 转换（唯一例外 Eve 裁剪 frontmatter）。Claude Code / Codex / Cursor 均原生实现 agentskills.io 标准，正文消费方式一致。**

### 3.1 CLI 侧落盘（`src/agents.ts`）

| 客户端 | 项目级 | 全局 |
|---|---|---|
| Claude Code | `.claude/skills/` | `~/.claude/skills/`（尊重 `CLAUDE_CONFIG_DIR`） |
| Codex | `.agents/skills/`（通用目录本身） | `~/.codex/skills/`（尊重 `CODEX_HOME`） |
| Cursor | `.agents/skills/`（通用目录本身） | `~/.cursor/skills/` |

项目级安装时非 universal agent 若配置目录不存在则跳过建 symlink，**唯独 claude-code 例外总是创建**。

### 3.2 客户端侧消费

- **Claude Code**：原生 Agent Skills，`/skill-name` 斜杠命令 + description 自动触发；声明遵循 agentskills.io 标准并在其上扩展（invocation control、subagent 执行、动态上下文注入）
- **Cursor**：从 `.agents/skills`、`.cursor/skills`、`~/.agents/skills`、`~/.cursor/skills` 自动加载，且「also loads skills from Claude and Codex directories」；支持 `scripts/`/`references/`/`assets/` 按需加载
- **Codex**：从 `$CWD/.agents/skills`、`$REPO_ROOT/.agents/skills`、`$HOME/.agents/skills`、`/etc/codex/skills` 发现；「Skills build on the open agent skills standard」，初始只载 name/description，激活后读全文，附属文件按需

### 3.3 对 skill 写法的约束

- 三客户端正文与 references 消费方式一致，**无单文件降级问题**
- README「Compatibility」矩阵：`allowed-tools` 多数客户端支持；`context: fork` 仅 Claude Code；Hooks 仅 Claude Code、Cline、Kiro CLI——「宿主无关」写法应只依赖规范六字段 + 各家触发控制扩展（§4），不依赖 Claude Code 独有正文特性

来源：
- https://github.com/vercel-labs/skills/blob/main/src/agents.ts 、README「Supported Agents」「Compatibility」
- https://code.claude.com/docs/en/skills
- https://cursor.com/docs/context/skills
- https://developers.openai.com/codex/skills （重定向至 learn.chatgpt.com/docs/build-skills）

---

## 4. 用户触发式 skill

**结论：可表达，但触发控制不在 agentskills.io 规范内、CLI 纯透传，语义由各客户端自行解释——Claude Code 与 Cursor 支持同名 frontmatter 字段 `disable-model-invocation: true`（仅 `/skill-name` 显式触发）；Codex 不认该字段，需在 skill 目录内附 `agents/openai.yaml` 设 `allow_implicit_invocation: false`；其余不识别的客户端忽略字段，skill 退化为模型自主触发。⚠️ 官方未对该字段做跨客户端承诺，description 措辞是必要兜底。**

- CLI 安装解析只识别 `name`、`description` 与 `metadata.internal`（`src/frontmatter.ts` 为极简 YAML-only 解析器；grep `disable-model-invocation`/`user-invocable` 在安装路径零命中），其余字段**原样透传**，无降级/转换逻辑
- agentskills.io 规范 frontmatter 仅六字段（`name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools`），无触发控制字段；文件系统客户端对未知字段的实践为忽略（硬报错仅发生在 claude.ai 上传/Skills API 打包路径，与 skills.sh 分发无关）
- **Claude Code**：`disable-model-invocation: true` → 「run only when you invoke them」，slash-command 式
- **Cursor**：同字段同语义——「is only included when you explicitly invoke via `/skill-name`」
- **Codex**：忽略该字段；支持 skill 目录内 `agents/openai.yaml`，`allow_implicit_invocation: false` 时「Codex won't implicitly invoke the skill based on user prompt」，用户仍可显式调用。该 yaml 随目录被 CLI 原样复制，与 frontmatter 方案可共存
- 相关机制：`npx skills use <source>@<skill>` 不安装、写临时目录并生成 prompt 到 stdout（可管道给 `claude`），`--agent` 直接拉起对应 agent——「一次性显式触发」的官方途径
- 先例：`mattpocock/skills` 的 `setup-matt-pocock-skills` 即经 skills.sh 分发的用户触发式接线 skill

来源：
- https://github.com/vercel-labs/skills/blob/main/src/frontmatter.ts 、README
- https://agentskills.io/specification （六字段清单）
- https://code.claude.com/docs/en/skills （`disable-model-invocation`）
- https://cursor.com/docs/context/skills （Cursor 的 `disable-model-invocation`）
- https://developers.openai.com/codex/skills （`agents/openai.yaml` 的 `allow_implicit_invocation`）
- https://www.aihero.dev/skills-setup-matt-pocock-skills （setup 类 skill 先例）

---

## 5. 更新机制

**结论：`npx skills update` 存在且可用，但无版本号/semver——更新语义是「内容哈希比对 + 整目录覆盖重装」，跟踪安装时 ref 的最新内容。**

### 5.1 来源记录（双 lockfile）

- 全局 `~/.agents/.skill-lock.json`（schema v3，或 `$XDG_STATE_HOME/skills/.skill-lock.json`）：记 `source`、`sourceType`、`sourceUrl`、`ref`、`skillPath`、`skillFolderHash`（GitHub tree SHA）、时间戳
- 项目级 `skills-lock.json`（v1，设计为提交进版本库）：按名排序、无时间戳以便 git 自动合并；hash 为磁盘文件内容的 SHA-256

### 5.2 更新判定与执行（`src/update.ts`）

- GitHub 源优先比 tree SHA；其他 git 源 clone 后比内容 SHA-256；well-known 源比 `digest`。纯内容哈希，无 semver
- **覆盖不是合并**：对有更新的 skill 子进程重跑 `skills add <sourceUrl> --skill <name> -y`（`shell: false`），安装路径先 `rm -rf` 目标目录再复制——用户本地改动被丢弃，skill 内容不应假设本地修改能存活更新
- 还会检测源仓库已删除的 skill 并提示删除；无法自动检查的源（本地路径、不可达仓库等）列出并提示手动重装
- scope：`-g` 仅全局、`-p` 仅项目、`-y` 免交互

来源：
- https://github.com/vercel-labs/skills/blob/main/src/skill-lock.ts 、`src/local-lock.ts` 、`src/update.ts`

---

## 6. 对 #1709 三层架构的可行性校验

| #1709 假设 | 结论 | 依据/约束 |
|---|---|---|
| skills.sh 发两个 skill（同仓库） | **成立** | 多 skill 仓库原生支持；`npx skills add <repo>` 交互全选，或 `<repo>@<skill-name>` / `--skill` 直指单个（§1.1、§1.2）。skill 目录须放安装源仓库浅层（≤3 层深），`name` 与目录名一致 |
| 工作流 skill 含 `references/*.md` 多文件 | **成立** | 整目录递归复制 + 规范定义的相对路径引用，三客户端均按需读取（§2、§3） |
| `setup-arcreel-skills` 仅用户显式触发 | **成立，但需双载体 + 措辞兜底 ⚠️** | 触发控制非规范语义、CLI 纯透传：Claude Code/Cursor 认 frontmatter `disable-model-invocation: true`，Codex 需另附 `agents/openai.yaml`（`allow_implicit_invocation: false`），其余客户端退化为模型自主触发；description 须写成「仅在用户明确要求 setup 时使用」做兜底（§4） |
| 用户 `npx skills update` 自行维护新鲜度 | **成立（无 semver）** | 内容哈希比对 + 覆盖重装（§5），与 #1709「不设版本握手、漂移由用户自管」吻合；skill 内容不要写死版本号承诺 |
| `agent-installation-guide.md` 引导 `npx skills add` | **成立** | 私有仓库亦可装（用户须有该仓库 git 访问权，§1.3）。若要面向无仓库权限的外部用户分发：独立公开 skills 仓库（update 体验最好）、服务端直链归档（≤10 MiB）或自托管 `.well-known/skills/` 端点均为 CLI 原生通道（§1.1） |

**派生到 Spec #1711 的两个约束**：

1. skill 目录布局：浅层放置、`name` 与目录名一致（小写连字符）；正文只用规范六字段 + 触发控制扩展，不依赖 `context: fork`、Hooks 等 Claude Code 独有特性
2. setup skill 同时携带 frontmatter 触发控制与 `agents/openai.yaml`，并在 description 里做语义兜底

---

## 7. 其他值得注意的事实

- 命令全集：`add`、`use`、`list/ls`、`find`（`--owner` 可扫 org）、`remove/rm`、`update`、`init`（模板只含 name/description）；实验性 `sync`
- 默认项目级安装，`-g` 全局；CLI 按文件系统痕迹自动检测本机已装 agent，单个自动选中、多个交互多选并记忆上次选择
- 安全设计：YAML-only frontmatter 解析（规避 eval RCE）、skill 名 sanitize 防路径穿越、下载/解压限额、update 子进程 `shell: false`
- Cursor 会同时读取 Claude/Codex 的 skill 目录，跨客户端目录互通程度高于 CLI 的 symlink 假设
- 官方示例 skill 仓库：vercel-labs/agent-skills；发布公告：Vercel changelog「Introducing skills, the open agent skills ecosystem」

来源：
- https://github.com/vercel-labs/skills （README、`src/cli.ts`、`src/add.ts`）
- https://vercel.com/changelog/introducing-skills-the-open-agent-skills-ecosystem
- https://cursor.com/docs/context/skills
