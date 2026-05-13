# Agent 沙箱化 + 项目级 Skill Overlay 提案

> 日期：2026-05-12
> 状态：Proposal (Pending)
> SDK 版本：claude-agent-sdk-python 0.1.80

---

## 目标

提升 ArcReel 智能体的自由度,同时不扩大 secrets 外泄面。具体两件事:

1. **接入 Claude Agent SDK 原生 sandbox**,让 Bash 工具从「精确路径白名单」解放为「沙箱内自由放行」,消除当前 agent 频繁调 Bash 被拒的体验问题
2. **支持视频项目级 skill / agent / CLAUDE.md 覆盖**,让用户可以为单个视频项目添加专属配置,而不污染其他项目

## 现状

ArcReel 已经做了大半基础工作:

- ✅ `agent_runtime_profile/` 双向隔离(智能体配置与开发态 `.claude/` 物理分离,Docker 部署零泄漏)
- ✅ `setting_sources=["project"]` 显式控制,屏蔽 `~/.claude/` 全局污染
- ✅ `PreToolUse` Hook + `_is_path_allowed` 路径围栏 + 写扩展名白名单(`.json/.md/.txt`)
- ✅ 声明式权限规则(settings.json deny / allow + Bash 精确路径白名单 + `canUseTool` 默认拒绝)
- ✅ SDK 0.1.80+ session 管理(`sdk_session_id` 已是业务标识)
- ✅ ConfigService 把 provider 配置从 JSON 迁到 DB

未做的两块:

- ❌ **没启用 SDK sandbox**:目前 `DEFAULT_ALLOWED_TOOLS` 不含 Bash,所有 Bash 调用必须在 settings.json 配精确路径白名单,新增 skill 脚本必须改 settings.json,且不支持探索性 Bash(如 `ls`、`cat`、`jq`、`python -c`)
- ❌ **视频项目无法持有自定义 skill**:所有项目共享 `agent_runtime_profile/.claude/skills/`,`projects/<name>/.claude` 只是 symlink。原始需求"不同项目可以有各自额外的 skill"未兑现

## 关键决策

### 决策 1:启用 SDK sandbox + `autoAllowBashIfSandboxed`,删除 Bash 白名单

沙箱内 Bash 在 cwd 范围自动放行,新增 skill 脚本不再需要改权限配置。

SDK 0.1.80 的 `SandboxSettings` 只管命令执行行为,不管文件和网络。文件/网络限制走 `permissions.deny`,扩展到当前漏掉的敏感文件(`projects/.arcreel.db`、`projects/.system_config.json.bak`、`agent_runtime_profile/.claude/settings.json` 等)。

macOS 自动用 Seatbelt,Linux 自动用 bubblewrap,启动开销 <50ms。

### 决策 2:Docker 部署启用 `enableWeakerNestedSandbox`

容器内 bwrap 默认起不来(unprivileged user namespace 被禁用),开启该选项后 bwrap 进入 reduced capability mode。

沙箱视角下损失 `/proc` 独立挂载与 PID namespace,文件系统隔离、网络代理隔离、进程隔离均保留。容器边界本身已经隔离宿主,且 secrets 不在 env(决策 4),`/proc` 暴露的剩余价值很低。这是 Anthropic 文档明确推荐的容器部署模式。

### 决策 3:项目级 overlay 采用 pure delta 模式

视频项目可以有自己的 CLAUDE.md(append 到 profile)、skill、subagent。

- **只存增量,不存完整副本**。profile 升级,项目无感跟随。
- **同名覆盖**:overlay 中存在的项,该项目使用 overlay 版本;其他项目继续用 profile。
- **生命周期只有两个动作**:用户加/改一个 overlay 文件、用户删一个 overlay 文件。**没有 sync / reset / 三向合并 / 版本号**。
- **不做**模板变量插值、可视化 diff,这些等真有需求再加。
- **导入导出**:overlay 是项目资产的一部分,导出 zip 携带,导入还原。项目运行时的内部数据(迁移 marker 等)不入归档。

### 决策 4:provider secrets 全面下线 `os.environ`

SDK 0.1.80 没有程序化 `api_key` 字段,认证只能走环境变量。但 SDK 子进程的 env 通过 `{**os.environ, **options.env, ...}` 合并构造(见 GitHub Issue #573 暴露的源码),`options.env` 可以覆盖父进程同名 env。

基于此:

- **父进程 `os.environ` 只保留启动级配置**(`AUTH_*` / `DATABASE_URL` / `LOG_LEVEL`),所有 provider secrets(`ANTHROPIC_*` / `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_*` / `GOOGLE_APPLICATION_CREDENTIALS`)不再写入 env。ConfigService DB 是唯一来源。
- **每次构造 SDK options**,从 DB 取 Anthropic 配置注入 `options.env`;同时把其他 provider 密钥用空值覆盖,防御性兜底任何残留路径。
- 用户切换 provider 配置后,下一次新建 session 或重连时生效。

Anthropic 认证如何让 SDK 子进程拿到、同时让 Bash 子进程不可见(见安全红线),属于设计方案决策,不在本提案范围。

### 决策 5:沙箱网络默认放行

不维护 WebFetch 域名白名单。

- 用户随时新增/编辑供应商,白名单维护不动且打断体验
- agent 查文档、下样例属于合理需求
- 决策 4 + 安全红线把 secrets 从 env、文件系统、Bash 子进程三路都拿走,真正的"先读敏感数据再外发"链路被切断,放开"出"不会新增攻击面

## 安全红线(硬性指标)

下列指标不可绕过,实施方案如不能同时满足全部红线,需求不成立。

- **Bash 子进程不可见任何 provider 密钥与认证密钥**(包括 Anthropic 自身)
- **agent 不能读取**`.env` / `projects/.arcreel.db` / `projects/.system_config.json.bak` / `vertex_keys/**` / `agent_runtime_profile/.claude/settings.json` 等敏感文件
- **agent 不能写项目目录外**
- **父进程 `os.environ` 不含 provider 密钥**

## 范围

### 本次涉及的模块

- SDK 配置构造层(SessionManager)
- 项目管理与目录结构(ProjectManager)
- 项目归档(project_archive)
- 配置服务与所有 provider env fallback 路径(ConfigService、`_load_project_env`、`sync_anthropic_env`、`SystemConfig` 兼容层等)
- 前端项目设置页(overlay 管理)
- 部署文档与 Dockerfile

### 非目标

- 写前 Checkpoint / rewind(SDK 0.1.80 的 `enable_file_checkpointing` 与 ArcReel 0.13.0 起用的 DB session store 互斥,需要单独评估技术路径后立项)
- 模板变量插值 / 三向合并 / 模板版本号 / sync API
- 外部 skill marketplace / 签名机制
- 全量目录快照 / shadow git
- DB 与媒体的回滚
- Bash / skill 脚本写入的回滚
- Landlock LSM / OS user 级隔离 / Firecracker
- WebFetch 黑名单

## 功能验收

**沙箱**
- agent 在项目目录内自由跑 `ls / cat / jq / python -c` 不被拒
- 新增 skill 脚本无需改权限配置
- 在安全红线全部验收通过的前提下,agent 可以自由 `curl` 任意域名

**项目级 overlay**
- novel-a 写"风格:武侠"只影响 novel-a,novel-b 不变
- novel-a 加专属 skill,在 novel-a 触发,在 novel-b 触发不到
- 同名 skill 在 novel-a 用 overlay 版本,novel-b 用 profile 版本
- profile 升级后所有项目无感跟随
- 项目导出 zip 携带 overlay,导入后还原

**环境变量隔离**
- 用户切换 Anthropic 配置后,下一次新建 session 或重连时生效

**部署**
- Docker 部署文档明确给出 sandbox 启用步骤,新部署开箱即用
- Linux + macOS 本地开发环境均能启动 sandbox
- 老项目通过迁移脚本能正常使用 overlay

## 前置调研项

实施前需先以最小 PoC 验证下列技术假设。结果直接影响方案设计:

1. **SDK Bash 子进程对 `options.env` 的继承行为**。决策 4 + 安全红线"Bash 不可见 secrets"的实现路径取决于此。
2. **SDK 0.1.80 跨平台(macOS/Linux)对 symlink / local plugin 的处理行为**。决策 3 的 overlay 加载机制取决于此。
3. **`autoAllowBashIfSandboxed=True` 是否足以让 Bash 工具实际可用**,在 `DEFAULT_ALLOWED_TOOLS` 不含 Bash 的现状下是否还需要调整 `allowed_tools` 或 `canUseTool` fallback。

## 风险

1. **Docker `enableWeakerNestedSandbox` 安全降级**:Anthropic 文档警告"considerably weakens security"。容器边界 + 权限 deny + 环境隔离构成三层兜底,综合可控。
2. **`os.environ` 残留 secrets**:`sync_anthropic_env`、`SystemConfig` 兼容层、`_load_project_env` 等多处历史写入点,遗漏会导致密钥被子进程继承。决策 4 的防御性空值覆盖兜底,但实施阶段需要全量审计。
3. **沙箱不兼容的 Bash 命令**(`docker`、`watchman` 等):ArcReel 用不到,影响小。如未来引入需走 `excludedCommands`。
4. **macOS Seatbelt 已 deprecated**:Anthropic 官方承认,短期无替代,跟随 Anthropic。

## 后续工作(明确推迟)

- 写前 Checkpoint / rewind(待 DB session store 与 SDK `enable_file_checkpointing` 兼容路径评估)
- Overlay 模板变量插值 / 可视化三向合并
- 外部 skill marketplace
- Landlock LSM 加固
- WebFetch 黑名单
- Bash / skill 脚本写入的回滚
