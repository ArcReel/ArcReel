# Agent 沙箱化 — 设计 Spec

> 日期：2026-05-12（实测调整 2026-05-13）
> 状态：Implemented，实测验收通过
> 关联：[2026-05-11 提案](../../proposals/2026-05-11-agent-sandbox-and-project-skill-overlay-proposal.md)
> SDK 版本：claude-agent-sdk-python 0.1.80
> 范围：提案中「决策 1 / 2 / 4 / 5 + 安全红线」的沙箱部分。决策 3「项目级 Skill Overlay」单独立 spec。

---

## 0. 实施后实测调整（2026-05-13）

下列偏离原始设计的事项在 manual acceptance 阶段实测确认，下文相关章节均反映**实测后**的最终方案。原始设计文本保留以便追溯决策线索。

### 0.1 SDK 0.1.80 的实测限制

| 假设 | 实测 |
|---|---|
| 「`settings.json` 中 `Read(//app/...)` deny rules 自动翻译给 Bash sandbox 内核级 file-read deny」 | **部分正确**。SDK CLI 会把 `permissions.deny` 中的 `Read(<path>)` 翻译给 sandbox profile，**但路径必须真实存在且为绝对路径**——CLI strings 含 `"Skipping non-existent deny path"`。`//app/.env` 在宿主开发态（Mac worktree）不存在，被静默跳过，导致 `cat ../../.env` 可读。**结论：宿主路径 deny 必须在代码里动态构造绝对路径，通过 `sandbox.filesystem.denyRead` 字段注入。** |
| 「`SandboxSettings.network.allowedDomains = ["*"]` 即可放行所有域名」 | **错**。SDK 0.1.80 网络代理只支持 `*.example.com` 这种 subdomain wildcard，`["*"]` 单独不被识别为"全放行"，触发 `X-Proxy-Error: blocked-by-allowlist`。**结论：决策 5「网络默认放行」在 SDK 当前版本下做不到，须维护白名单**。 |
| 「敏感文件读由 sandbox profile 拦下，hook 不必兜底 Bash 路径」 | 反向也正确——hook 拦不住 Bash（agent 用 `Bash("cat ...")` 走 Bash 工具不经 `_is_path_allowed`）。两条路径职责互补：**Bash 由 sandbox profile 拦，SDK Read/Write/Edit/Glob/Grep 由 hook 拦**。 |
| 「`OTHER_PROVIDER_ENV_KEYS` 用空值覆盖即可对齐"Bash 不见 provider 密钥"红线」 | **部分正确**。空值覆盖让真值消失，但变量**名**仍在 Bash subshell 可见（`env \| grep ARK_API_KEY` 返回 `ARK_API_KEY=`），acceptance 严格读视为 noise；同时实测发现 SDK 子进程还可能继承宿主或 CLI 自身注入的密钥变量（如 `GEMINI_CLI_IDE_AUTH_TOKEN`），固定清单兜不住。**结论：用 PreToolUse Bash hook `env -u` 包装命令，固定清单 + 动态模式匹配两路汇总。** |

### 0.2 红线 2 范围调整：`.arcreel.db` 暂从 `denyRead` 清单移除

skill 脚本通过 `lib.generation_queue_client` 直连本地 SQLite (`projects/.arcreel.db`) 入队任务——sandbox 把 db 加进 `denyRead` 会让任务队列功能整体失效。当前**接受 db 可被 sandbox 内进程读**（含明文 `agent_credential` / `credential` / `custom_provider` 的 `api_key` 字段）的限制。

**后续路径**：把入队链路改造为 SDK 原生 tool（in-process MCP server），agent 直接调 tool 而不再 Bash → python 脚本。sandboxing.md §"What sandboxing does not cover"明确"sandbox 只隔离 Bash 子进程"——SDK Custom Tool 的 callback 跑在 server 主进程上下文，**不经 sandbox-exec/bwrap 包装**。改造完成后 db 可重新加回敏感清单。已开 follow-up issue。

### 0.3 决策 5 调整：网络白名单 + ENV 扩展

`sandbox.network.allowedDomains` 默认覆盖：
- ArcReel 内置 provider 域名：`anthropic.com` / `*.anthropic.com` / `*.volces.com` / `x.ai` / `*.x.ai` / `*.googleapis.com` / `*.google.com` / `vidu.cn` / `*.vidu.cn` / `*.viduai.cn`
- 常见 dev 域名：`code.claude.com` / `github.com` / `*.github.com` / `*.githubusercontent.com` / `pypi.org` / `*.pypi.org` / `*.npmjs.org` / `registry.yarnpkg.com` / `example.com`

`ARCREEL_SANDBOX_EXTRA_ALLOWED_DOMAINS`（逗号分隔）允许运维补充自定义 provider 域名。**spec 第 § 2 决策 5 文字相应修订**。

### 0.4 新增：`allowUnsandboxedCommands: false`

文档 sandboxing.md §Best practices 建议生产部署关掉 escape hatch，防 agent 通过 `dangerouslyDisableSandbox` 参数请求绕过 sandbox。已加。

### 0.5 跨项目读隔离 bug 修复

`_is_path_allowed` 规则 1 原判断"路径在 `projects/` 下且不在 cwd 内 → 跨项目拒"，把 `projects/` 根下直放的文件（如 `.arcreel.db`）误判为跨项目读。修正为"路径落入 `projects/<某子目录>/` 才算跨项目"，根下直放文件放行。

---

## 1. 目标

落地 claude-agent-sdk 0.1.80 的原生 Bash 沙箱，让 agent 在项目 cwd 内自由跑 `ls / cat / jq / python -c / curl`，同时把 provider 密钥从 `os.environ` 全面下线，达到提案安全红线四条：

1. Bash 子进程不可见任何 provider 密钥（含 Anthropic 自身） — 实测调整见 §0.1（第 4 行）
2. agent 不能读取 `.env` / `projects/.system_config.json{,.bak}` / `vertex_keys/**` / `agent_runtime_profile/.claude/settings.json` — `projects/.arcreel.db*` **暂不在范围**，见 §0.2
3. agent 不能写项目目录外
4. 父进程 `os.environ` 不含 provider 密钥

## 2. 关键决策（沿用提案）

- 决策 1：启用 SDK sandbox + `autoAllowBashIfSandboxed=True`，删除 Bash 精确路径白名单
- 决策 2：Docker 部署启用 `enableWeakerNestedSandbox=True`
- 决策 4：provider secrets 全面下线 `os.environ`，DB 为唯一真相源；ANTHROPIC_* 通过 `options.env` 注入 SDK 子进程；其他 provider 用空值覆盖兜底 + PreToolUse Bash hook `env -u` 包装把变量名一并从 Bash subshell 剥离（实测扩展，见 §0.1）
- 决策 5：~~沙箱网络默认放行~~ → **维护内置 provider + 常用 dev 域名白名单，ENV `ARCREEL_SANDBOX_EXTRA_ALLOWED_DOMAINS` 扩展**（SDK 0.1.80 不支持 `["*"]` 全放行，见 §0.3）

## 3. 本次新增决策（spec 阶段）

- **沙箱不可用即硬失败**：macOS / Linux / Docker 全部环境强制要求 sandbox 工具可用，不存在「降级到旧 Bash 白名单」代码路径
- **启动断言一视同仁**：dev / test / 生产全部跑 `assert_no_provider_secrets_in_environ()`，命中红线即拒绝启动
- **PreToolUse 文件围栏精简为三条普适规则**：跨项目读拒、Write/Edit cwd 外拒、cwd 内代码扩展名写拒。settings.json `permissions.deny` 仅保留敏感文件读/写禁，普适规则不再用 deny rules 枚举（Level B 收敛方案）
- **`settings.json permissions.allow` 整段清空**：工具白名单集中到 `DEFAULT_ALLOWED_TOOLS` 一处声明
- **backend env fallback 全面清理**：所有 `api_key or os.environ.get(...)` 模式改为只接受显式参数，缺失即 raise

---

## 4. 四层防线架构

| 层 | 防什么 | 实现位置 |
|---|---|---|
| **L1** 父进程 env | secrets 不在 `os.environ` 内存里被任意子进程继承 | `lib/config/service.py` 删 `os.environ` 写入；`server/auth.py / _load_project_env` 缩范围；启动 assertion |
| **L2** SDK 子进程 env | SDK 拿到 Anthropic 认证；其他 provider env 空值覆盖 | `SessionManager._build_options()` 注入 `options.env` |
| **L3** Bash 沙箱 (Seatbelt / bwrap) | cwd 外不可写、敏感文件不可读、Bash 自由 | `SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True)` + Docker `enableWeakerNestedSandbox=True` |
| **L4a** Permission rules (settings.json) | 仅敏感文件读/写禁 —— sandbox 唯一识别敏感文件的方式 | `permissions.deny` 仅列敏感文件；`allow` 整段清空 |
| **L4b** PreToolUse hook | SDK 内置工具（不经过 sandbox）的动态围栏 | hook 三件事：(1) 跨项目读拒；(2) Write/Edit cwd 外一律拒；(3) cwd 内写代码扩展名拒；保留 JSON 校验 |
| **L4c** `allowed_tools` (代码) | 工具白名单集中声明 | 加入 `Bash`/`BashOutput`/`KillBash`，其余保留 |

设计要点：
- L3 是新增核心防线（让 Bash 自由）
- **sandbox 只管 Bash 子进程**。SDK 内置 Read/Write/Edit/Glob/Grep 在 SDK 子进程内直接系统调用，不经过 sandbox —— 必须靠 L4a deny rules + L4b hook 拦
- SDK 文档（`claude_agent_sdk/types.py:875-882`）明确：sandbox 启用后，**Bash 子进程的**文件读限制翻译自 `Read(<path>)` deny rules、写限制翻译自 `Edit(...)` deny rules、网络限制翻译自 `WebFetch(...)` rules
- L4a 与 L4b 职责不重叠：deny rules 管「特定文件不能读/写」（静态路径模式 + 同时翻译给 sandbox profile），hook 管「按 cwd / 扩展名的普适规则」（动态运行时判断）

---

## 5. 改造点（按文件分组）

### 5.1 `server/agent_runtime/session_manager.py`

| 改动 | 内容 | 理由 |
|---|---|---|
| `DEFAULT_ALLOWED_TOOLS` | 加入 `Bash` / `BashOutput` / `KillBash` | 工具白名单集中到代码 |
| `_build_options()` | 新增 sandbox 设置含以下字段（实测调整，原始设计只列 enabled/autoAllowBashIfSandboxed/enableWeakerNestedSandbox）：`enabled=True` / `autoAllowBashIfSandboxed=True` / `enableWeakerNestedSandbox=in_docker()` / `allowUnsandboxedCommands=False`（关 escape hatch）/ `network.allowedDomains=_build_sandbox_allowed_domains()`（保守白名单 + ENV 扩展，见 §0.3） / `filesystem.denyRead=_build_sensitive_abs_paths()`（动态构造真实绝对路径，见 §0.1）；新增 `env=self._build_provider_env_overrides()` | 决策 1+2+4+5 落地点 |
| 新增 `_bash_env_scrub_hook` | PreToolUse 钩 Bash 工具，把 command 包成 `env -u VAR ... sh -c '<orig>'` 剥离所有 provider 密钥变量（固定清单 + 动态匹配 `*API_KEY*` / `*AUTH_TOKEN*` / `*CREDENTIAL*` 等模式） | §0.1 红线 1 实测扩展 |
| 新增 `_build_sensitive_abs_paths()` | 枚举 worktree 下真实存在的敏感文件绝对路径，给 sandbox profile 的 file-read deny 用；SDK CLI 会跳过不存在路径，所以宿主开发态必须动态构造 | §0.1 实测发现 |
| 新增 `_build_sandbox_allowed_domains()` | 默认清单 + `ARCREEL_SANDBOX_EXTRA_ALLOWED_DOMAINS` ENV 扩展 | §0.3 |
| 新增 `_build_provider_env_overrides()` | 见 §6.2 注入清单 | 决策 4 实施 |
| 新增模块级 `check_sandbox_available()` | macOS 判 `shutil.which("sandbox-exec")`；Linux 判 `shutil.which("bwrap")`；不可用 `raise RuntimeError` | 启动期硬失败检测 |
| 新增模块级 `detect_docker_environment()` | 检查 `/.dockerenv` 存在 或 `/proc/1/cgroup` 含 `docker`/`podman`。**启动期一次性检测**，结果缓存到 `SessionManager._in_docker`（容器内/外运行时不会变） | 自动开 `enableWeakerNestedSandbox` |
| `_is_path_allowed()` | 三条普适规则：(1) `Read/Glob/Grep` 路径在 `project_root/projects/` 下但不在 cwd 内 → 拒（跨项目读隔离）；(2) `Write/Edit` 目标超出 cwd → 拒；(3) `Write/Edit` 在 cwd 内但扩展名为 `.py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml` → 拒（agent 不写代码）。SDK tool-results / `/tmp/claude-*/tasks` 保留例外 | 替代原 settings.json 中 22 条目录/扩展名 Edit/Write deny rules |
| `_WRITABLE_EXTENSIONS` 改名 `_CODE_EXTENSIONS_FORBIDDEN` | 语义反转：原本是「只允许这些扩展名」白名单，改为「禁止这些扩展名」黑名单 | 让 agent 在 cwd 内可写任意非代码文件，对齐 Level B 简化 |
| `_can_use_tool` deny hint | 删旧 Bash 白名单文案；新文案示例：`未授权的工具调用: {tool_name}({args})\n上游决策原因: {reason}`，不再列举具体白名单 | 沙箱化后白名单文案过时 |

### 5.2 `agent_runtime_profile/.claude/settings.json`

> **实测调整（见 §0.1）**：原方案靠 settings.json 的 `Read(//app/...)` deny rules 给 sandbox profile 翻译路径，但 SDK CLI 跳过不存在路径——`//app/` 在宿主开发态（Mac worktree）不存在，deny 不生效。**最终方案**：settings.json 的 `permissions.deny` 整段清空，敏感文件防线**全部**由 §5.1 `sandbox.filesystem.denyRead`（代码动态注入真实绝对路径，覆盖宿主 + Docker 双平台）+ `_is_sensitive_path` hook（SDK Read/Write/Edit/Glob/Grep 工具兜底）负责，单一真相源在 `SessionManager._SENSITIVE_RELATIVE_*` 类常量。

settings.json 改造后内容（仅留空对象，保留文件本身让 `setting_sources=["project"]` 加载不报错）：

```json
{}
```

为什么不放 deny rules：
- settings.json 里的 deny 路径是静态字符串，无法用 `self.project_root` 动态拼，宿主开发态路径不固定（`/Users/<user>/ArcReel`、worktree 子路径等）—— 静态写无法命中
- Docker 部署里代码侧 `_build_sensitive_abs_paths()` 通过 `self.project_root.resolve()` 拿到的就是 `/app`，与原 `//app/...` 完全等价覆盖
- 单一真相源（`SessionManager._SENSITIVE_RELATIVE_FILES` / `_SENSITIVE_RELATIVE_PREFIXES` / `_SENSITIVE_RELATIVE_GLOBS`）避免清单漂移
- 普适规则「cwd 外写禁」「cwd 内写代码扩展名禁」由 §5.1 PreToolUse hook 实施
- Bash 子进程 cwd 外写由 sandbox 默认行为兜底（cwd 内可写、cwd 外只读）
- `allow` 整段也不需要：`Bash` / `BashOutput` / `KillBash` / `Read` / `Grep` / `Glob` 由 `SessionManager.DEFAULT_ALLOWED_TOOLS` 声明

### 5.3 secrets 下线 — `lib/config/service.py` 与全部调用方

| 文件:位置 | 改动 |
|---|---|
| `lib/config/service.py:29-75` `sync_anthropic_env()` | **整段删除函数体**；保留 `_ANTHROPIC_ENV_MAP` 字典供 SessionManager 引用 |
| `server/app.py:173-176` | 删除启动时 `sync_anthropic_env(session)` 调用 |
| `server/routers/agent_config.py:205,232,273` | 3 处 `sync_anthropic_env()` 调用全删 |
| `server/routers/system_config.py:380` | 1 处 `sync_anthropic_env()` 调用删 |
| `lib/system_config.py:228,376-384` | `_baseline_env` 字段删；`_set_env` / `_restore_or_unset` 不再写 `os.environ` |
| `server/agent_runtime/service.py:1024-1035` `_load_project_env()` | 在 `load_dotenv()` 后立刻执行**保守黑名单**清理：`os.environ.pop()` 掉 `ANTHROPIC_ENV_KEYS + OTHER_PROVIDER_ENV_KEYS` 中的精确 key（来自 `lib/config/env_keys.py` 单一真相源），其余 key（`AUTH_*`/`DATABASE_URL`/`RANDOM_VAR`/未来新增的非 provider 配置）原样保留。**注入清单与黑名单同源** —— `lib/config/env_keys.py` 同时供 §6.2 注入和此处 pop 使用 |

> **黑名单 vs 白名单的取舍**：早期方案曾考虑 `AUTH_* / DATABASE_URL / ...` 白名单过滤，但存在「白名单漏列合法运行时配置 → 误杀」风险。保守黑名单换走了「未来新增 provider key 漏列 → 泄漏」风险（漏列只意味着新 key 暂时绕过 pop 清理，并不会绕过 §6.2 `options.env` 空值覆盖兜底；而 6 个真密钥已被 §7.2 启动断言 `assert_no_provider_secrets_in_environ()` 兜底硬失败）。两害相权取其轻，选黑名单。

**保留**：`server/auth.py:189` `os.environ["AUTH_PASSWORD"] = password` —— 属 AUTH 子系统兼容路径，非 provider。

### 5.4 backend env fallback 清理（决策 4 配套清理）

把所有 `api_key or os.environ.get(...)` fallback 改为只接受显式参数，缺失即 `raise ValueError("请到系统配置页填写 <provider 名> API Key")`（实际文案按 provider 替换，如 "Ark API Key"、"Gemini API Key"）：

| 文件:行 | 改动 |
|---|---|
| `lib/ark_shared.py:21` | 删 env fallback |
| `lib/grok_shared.py:49` 周围 | 删 env fallback |
| `lib/vidu_shared.py:54-59` | 删 env fallback + 删 `allow_env_fallback` 参数 |
| `lib/image_backends/gemini.py:52,81,85` | 删 `GEMINI_IMAGE_MODEL`/`GEMINI_API_KEY`/`GEMINI_BASE_URL` env fallback |
| `lib/video_backends/gemini.py:54,80,84` | 同上 |
| `lib/text_backends/gemini.py:37` 周围 | 同上 |

backend 构造方（`lib/text_backends/factory.py` / `server/services/generation_tasks.py`）**无需修改** —— 已经显式从 `ConfigResolver.provider_config()` → DB 取 `api_key` 传入。

### 5.5 Docker 与部署文档

| 文件 | 改动 |
|---|---|
| `Dockerfile` | `apt-get install -y bubblewrap socat` 到运行镜像层（socat 用于 Linux sandbox 网络隔离） |
| 部署文档 | 新增「Linux 本地开发必须 `sudo apt install bubblewrap socat`」段；说明 sandbox 启动失败硬退出 |
| `agent_runtime_profile/CLAUDE.md` | 移除「使用相对路径调 skill 脚本」引导，改为说明沙箱化后 Bash 可在 cwd 内自由运行 |

---

## 6. 数据流

### 6.1 配置变更流（用户在 WebUI 切换 Anthropic 凭据）

```
用户提交新 credential
    ▼
POST /api/v1/agent/credentials/...
    ▼
ConfigService.create_or_update_credential()
    ▼
CredentialRepository.save() ──► DB (加密)
    ▼
200 OK，UI 提示「下一次新建 session 或重连时生效」
```

与现状的差别：
- 删除 `sync_anthropic_env(session)` 调用 —— 不再写 `os.environ`
- 已存在 session 仍用 spawn 时的 env 值（修复了一个隐性 bug：现状下表面"立即生效"但 SDK 子进程 env 实际固定）
- 何时生效摆到台前：**下一次新建 session 或重连**

### 6.2 Session 启动流 — 唯一的 Anthropic env 注入点

`SessionManager._build_options()` 调用 `_build_provider_env_overrides()`，返回：

```python
{
    # —— Anthropic 注入（真值，从 DB 取）——
    "ANTHROPIC_API_KEY":             cred.api_key,
    "ANTHROPIC_BASE_URL":            cred.base_url or "",
    "ANTHROPIC_MODEL":               cred.model or "",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": cred.haiku_model or "",
    "ANTHROPIC_DEFAULT_SONNET_MODEL":cred.sonnet_model or "",
    "ANTHROPIC_DEFAULT_OPUS_MODEL":  cred.opus_model or "",
    "CLAUDE_CODE_SUBAGENT_MODEL":    cred.subagent_model or "",

    # —— 其他 provider：空值覆盖（决策 4 防御性兜底）——
    "ARK_API_KEY":                   "",
    "XAI_API_KEY":                   "",
    "GEMINI_API_KEY":                "",
    "VIDU_API_KEY":                  "",
    "GOOGLE_APPLICATION_CREDENTIALS":"",
    "GEMINI_BASE_URL":               "",
    "GEMINI_IMAGE_MODEL":            "",
    "GEMINI_VIDEO_MODEL":            "",
    "GEMINI_IMAGE_BACKEND":          "",
    "GEMINI_VIDEO_BACKEND":          "",
    "VERTEX_GCS_BUCKET":             "",
    "FILE_SERVICE_BASE_URL":         "",
    "DEFAULT_VIDEO_PROVIDER":        "",
}
```

实施期把此清单与 `lib/system_config.py:_ENV_KEYS` 合并为单一常量，建议位置 `lib/config/env_keys.py`。

子进程派生链：

```
父进程 (FastAPI)  ─► SDK 子进程 (CLI)  ─► Bash 沙箱子进程
   不含 secrets       env = {**os.environ,        env 行为待 PoC #1
                            **options.env, ...}    验证（见 §8）
                       含 ANTHROPIC_*，其余空
```

### 6.3 父进程 `os.environ` 残留清理

清理路径已在 §5.3 列出。补充：

启动期 `assert_no_provider_secrets_in_environ()` 放在 FastAPI lifespan startup hook 最前，先于 ConfigService 初始化、先于 `_load_project_env` —— 任何后置代码若试图回写 provider env，下次启动会拒绝。

---

## 7. 启动检测与错误处理

### 7.1 启动序列（FastAPI lifespan）

```
uvicorn 启动 server.app:app
    ▼
lifespan startup
    ├─► [1] assert_no_provider_secrets_in_environ()
    │       命中 → RuntimeError，进程退出
    ├─► [2] check_sandbox_available()
    │       macOS: sandbox-exec 缺失 → RuntimeError
    │       Linux: bwrap 缺失 → RuntimeError
    ├─► [3] detect_docker_environment()
    │       /.dockerenv 存在 或 /proc/1/cgroup 含 docker/podman
    │       结果设置到 SessionManager._in_docker
    └─► 正常进入服务
```

### 7.2 父进程 assertion 实现

```python
def assert_no_provider_secrets_in_environ() -> None:
    """父进程禁止持有任何 provider 密钥。违反即 fail-fast。"""
    forbidden = {
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
    leaked = sorted(k for k in forbidden if os.environ.get(k))
    if leaked:
        raise RuntimeError(
            f"SECURITY: 父进程 os.environ 含 provider 密钥: {leaked}. "
            "请到 WebUI 系统配置页填写，并从 env / .env 中移除对应条目。"
        )
```

集合基于代码 grep（决策 4 红线对应的真密钥 keys）：
- `ANTHROPIC_API_KEY` — SDK 子进程读
- `ARK_API_KEY` — `lib/ark_shared.py:21`
- `XAI_API_KEY` — `lib/grok_shared.py:49`
- `GEMINI_API_KEY` — `lib/image_backends/gemini.py:81` / `lib/video_backends/gemini.py:80`
- `VIDU_API_KEY` — `lib/vidu_shared.py:59`
- `GOOGLE_APPLICATION_CREDENTIALS` — Google SDK 隐式 fallback 路径

非密钥的 provider 配置 env（如 `GEMINI_BASE_URL`、`VERTEX_GCS_BUCKET`）不在 assertion 集合 —— 由 §6.2 options.env 空值覆盖兜底。

### 7.3 启动失败的退出信息（结构化）

```
SANDBOX_UNAVAILABLE on linux
  sandbox-exec: n/a (not macOS)
  bwrap:        not found in PATH
Required for ArcReel agent runtime. Install bubblewrap:
  Ubuntu/Debian: sudo apt install bubblewrap
  Arch:          sudo pacman -S bubblewrap
```

### 7.4 运行时错误路径

| 场景 | 现象 | 处理 |
|---|---|---|
| `SandboxSettings` 构造期 SDK 抛错 | `_build_options()` 抛异常 | 透传到 `create_or_resume_session()`，SSE 推 `runtime_status: error`，session 不创建 |
| DB 取 Anthropic credential 失败 | `_build_provider_env_overrides()` 抛错 | 同上 — 不允许 fallback 到旧 `os.environ`（已无 secrets） |
| Bash 子进程命中 sandbox 拒绝 | sandbox 返回 EACCES / SIGKILL | 工具结果含 violation 信息，agent 自行重试或汇报 |
| PreToolUse hook 拦截跨项目读 | `permissionDecision: deny` | hook 现有路径，stream 告诉 agent 拒因 |

### 7.5 沙箱失效降级明确禁用

为防未来误改：

- 不提供 `ARCREEL_DISABLE_SANDBOX` 或类似环境开关
- 不提供 `SandboxSettings(enabled=False)` 的代码路径（`_build_options()` 写死 `enabled=True`）
- 不存在「沙箱不可用时回退到旧 Bash 白名单」的代码

任何后续 PR 试图加这些开关需要重新评估安全红线。

---

## 8. PoC 前置调研（spec → plan 之间执行）

> **状态（2026-05-13）**：PoC #1-6 已被合并前 manual acceptance 覆盖并通过；实测发现的偏差汇总在 §0。PoC #1（`options.env` 是否被 Bash 子进程继承）阳性确认，spec 增补了 PreToolUse Bash env scrub hook（见 §0.1 / §5.1 `_bash_env_scrub_hook`）。`scripts/dev/sandbox_poc.py` 与 `*.poc-report.md` 已在合并阶段删除，下表保留为设计意图记录。

| # | 平台 | 验证项 | 期望 | 失败处置 |
|---|---|---|---|---|
| 1 | macOS + Linux | `options.env` 注入 `ANTHROPIC_API_KEY=POC_TOKEN`，agent Bash 跑 `env \| grep POC_TOKEN` | **看不到** | 阳性 → spec 增补 PreToolUse Bash hook 剥离 `ANTHROPIC_*` env |
| 2 | macOS + Linux + Docker | sandbox enabled，agent Bash 跑 `cat /app/.env` / `cat /app/projects/.arcreel.db` | 拒绝（permission denied / sandbox violation） | 阳性 → 加 `Read(...)` deny rule；阴性（拒绝失败）→ 重审决策 1 可行性 |
| 3 | macOS + Linux + Docker | sandbox enabled + `autoAllowBashIfSandboxed=True`，agent 跑 `ls / jq / python -c 'print(1)'` | 放行，无 permission prompt | 阴性 → 确认 `Bash` 已在 `allowed_tools`；仍失败 → 排查 SDK 行为 |
| 4 | macOS + Linux + Docker | sandbox enabled，agent 跑 `curl https://example.com` | 放行（默认网络放行） | 阴性 → 显式设置 `network.allowedDomains=["*"]` |
| 5 | macOS + Linux + Docker | sandbox enabled + `Edit(//app/lib/**)` deny，agent 跑 `echo x > /app/lib/test.py` | 拒绝 | 阴性 → 重审决策 1 |
| 6 | Docker only | `enableWeakerNestedSandbox=True`，重跑 #3-5 | 同 #3-5 | 阴性 → 验证 Docker 镜像 bwrap 安装 |

PoC #1 是关键分支点：阳性时 spec 需扩展 PreToolUse Bash hook（在工具调用前剥 `ANTHROPIC_*`）；阴性时设计闭合。

---

## 9. 测试策略

### 9.1 单元测试新增

| 测试文件 | 内容 |
|---|---|
| `tests/agent_runtime/test_session_manager_sandbox.py` | (1) `_build_options()` 返回的 options 含 `sandbox=SandboxSettings(enabled=True)`；(2) Docker 环境下 `enableWeakerNestedSandbox=True`；(3) `options.env` 含 Anthropic 真值 + 其他 provider 空值 |
| `tests/agent_runtime/test_path_isolation_hook.py` | 新版 `_is_path_allowed` 三规则：(1) `Read/Glob/Grep` cwd 内通过、跨项目 `projects/<other>/` 拒；(2) `Write/Edit` cwd 外拒；(3) `Write/Edit` cwd 内代码扩展名（`.py/.js/.ts/.tsx/.sh/.yaml/.yml/.toml`）拒，非代码扩展名通过；(4) SDK tool-results / `/tmp/claude-*/tasks` 读例外 |
| `tests/server/test_startup_assertions.py` | `assert_no_provider_secrets_in_environ()` 命中 6 个 key 任一即 raise；`check_sandbox_available()` 双平台行为 |
| `tests/config/test_no_env_writes.py` | mock SessionManager 构建期，断言不再调 `sync_anthropic_env`；调 `_build_provider_env_overrides()` 后 `os.environ` 不变 |
| `tests/backends/test_no_env_fallback.py` | 构造 backend 时不传 api_key，断言 raise ValueError；不再读 env |

### 9.2 集成测试

> **状态（2026-05-13）**：原计划的 `tests/integration/test_sandbox_e2e.py` skeleton 已删除——所有场景都需要 sandbox 工具可用环境与真实 Anthropic credential，无法在 CI 跑；合并前已由 manual acceptance 覆盖（含红线 + 功能两段）。后续 CI 引入 sandbox runner 时可重建。下列场景保留作为 e2e 测试设计意图。

1. 启 session，agent 跑 `Bash("ls /app/projects/<self>/")` → 成功
2. agent 跑 `Bash("cat /app/.env")` → 输出含 sandbox violation
3. agent 跑 `Bash("curl -s https://api.github.com")` → 成功
4. agent 跑 `Read({"file_path": "/app/projects/<other>/project.json"})` → hook deny
5. agent 跑 `Write({"file_path": "/app/projects/<self>/test.py", "content": "..."})` → hook 拒（代码扩展名）
6. agent 跑 `Write({"file_path": "/app/lib/foo.json", "content": "..."})` → hook 拒（cwd 外写）

### 9.3 安全红线验收（合并门槛）

| 红线 | 验收方法 | 自动化 |
|---|---|---|
| Bash 子进程不可见 provider 密钥 | PoC #1 + `test_no_env_fallback.py` | ✅ |
| agent 不能读 `.env` / `.system_config.json.bak` / `vertex_keys/**` / `agent_runtime_profile/.claude/settings.json` | PoC #2 + 集成 #2 | ✅ |
| agent 不能写项目目录外 | 集成 #5 + sandbox 默认行为 | ✅ |
| 父进程 `os.environ` 不含 provider 密钥 | 启动 assertion 自检 + `test_startup_assertions.py` | ✅ |

> 已知限制：`projects/.arcreel.db` 暂时未纳入 denyRead（skill 入队脚本需要直读 SQLite），由 issue #519 跟踪。该 db 内的 provider 密钥目前依赖应用层"不在 Bash 中导出"防线，而非内核级阻断。

### 9.4 功能验收（对齐提案）

| 项 | 方法 |
|---|---|
| agent 在项目目录内自由跑 `ls / cat / jq / python -c` 不被拒 | 手动 + 集成 #1 |
| 新增 skill 脚本无需改权限配置 | 手动：临时加一个 echo skill，直接调用，应放行 |
| agent 可自由 `curl` 任意域名 | 集成 #3 + 手动多域名尝试 |
| 用户切换 Anthropic 配置后，下一次新建 session 或重连时生效 | 手动：切换 → 旧 session 用旧值 → 新 session 用新值 |
| Linux + macOS 本地开发均能启动 sandbox | CI 双平台 |
| Docker 部署文档明确给出 sandbox 启用步骤 | 文档评审 |

### 9.5 回归基线

修改前跑现有 `tests/agent_runtime/` 全部用例，记录基线。改造后必须全绿（除非测试自身因 spec 改动失效，需在 PR 描述逐条解释）。

特别关注：
- 现有 `test_session_manager.py` 涉及 `DEFAULT_ALLOWED_TOOLS` / settings.json allow 的断言需更新
- 现有 hook 测试涉及 `_WRITABLE_EXTENSIONS` 需改为新常量 `_CODE_EXTENSIONS_FORBIDDEN`，断言语义反转（原「只允许 .json/.md/.txt」变为「只禁止代码扩展名」）

---

## 10. 非目标 / 推迟

继承提案非目标，沙箱 spec 不涉及：

- 项目级 skill / agent / CLAUDE.md overlay（决策 3，独立 spec）
- 写前 Checkpoint / rewind
- WebFetch 黑名单 / 域名白名单
- Landlock LSM / OS user 级隔离 / Firecracker
- macOS Seatbelt 替代方案（已 deprecated 但短期无替代，跟随 Anthropic）

---

## 11. 风险

| 风险 | 来源 | 缓解 |
|---|---|---|
| Docker `enableWeakerNestedSandbox` 安全降级 | Anthropic 文档警告 | 容器边界 + permission deny + 环境隔离三层兜底 |
| `os.environ` 残留 secrets 实施期审计遗漏 | 多处历史写入点 | 启动 assertion 兜底 + `test_no_env_writes.py` 持续守护 |
| Sandbox 不兼容的 Bash 命令（`docker` / `watchman` 等） | SDK 文档 | ArcReel 不用；未来引入需走 `excludedCommands` |
| PoC #1 结果阳性 → 需扩展 Bash hook 剥离 env | SDK 子进程 env 继承行为不明 | spec 已留扩展路径，plan 阶段并入 |
| 用户已存在 .env 含 provider 密钥导致启动失败 | 历史配置 | 启动错误提示明确引导到 WebUI；文档说明迁移步骤 |
| `.arcreel.db` 含明文 provider 密钥仍可被 sandbox 内 skill 脚本读取 | §0.2 实测限制 | 已开 follow-up issue：把 generation queue 入队链路改造为 SDK 原生 tool（in-process MCP），让 agent 不再 Bash → python 脚本路径访问 db。改造完成后 db 加回 `denyRead`。当前缓解：`agent_credential` / `credential` / `custom_provider` 表的 `api_key` 字段保持 application-level 加密的优先级提升 |
| 用户自定义 provider 域名未在默认白名单内 → skill HTTP 请求被沙箱拦 | §0.3 SDK 限制 | `ARCREEL_SANDBOX_EXTRA_ALLOWED_DOMAINS` ENV 扩展。后续 PR 加"新域请求 → 用户确认 → 永久放行"流，避免运维手动维护 |
