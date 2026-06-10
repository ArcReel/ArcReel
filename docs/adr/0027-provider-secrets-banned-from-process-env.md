---
status: accepted
---

# Provider 密钥禁入进程环境：启动断言 + agent env 覆盖 + Bash env -u 清洗

provider 密钥若进 `os.environ`，会被 agent 子进程整体继承，泄漏面大，且进程级单值与多凭证/按会话切换冲突。决定密钥只存 DB，三层强制：①启动断言——`os.environ` 含任何 provider 真密钥（`PROVIDER_SECRET_KEYS`）即 fail-fast 拒绝启动，以环境变量配置 key 的部署将无法启动，这是刻意设计；②agent 子进程 env——Anthropic 凭证按会话从 DB 注入（见 `docs/adr/0017`），其余 provider 变量一律空串覆盖；③Bash 命令被 PreToolUse hook 包装成 `env -u … sh -c`，按固定清单 + `*_API_KEY`/`*_AUTH_TOKEN` 等模式扫描动态 unset，拦截任何仍从宿主环境继承的敏感变量。

## Consequences

- 新增供应商时须把它的 env 变量名登记进 `lib/config/env_keys.py`（启动断言/env 覆盖/Bash 清洗共用此清单；模式扫描只能覆盖命名规整的变量）。
- ③ 是 POSIX 机制（依赖 `env`/`sh`），只为 macOS/Linux 沙箱路径设计；Windows 原生回退下不适用，且该 hook 与 Bash 前缀白名单的权限链交互存在已知缺陷，修复在 issue #622 跟踪——①② 两层不受平台影响。
- 与 `docs/adr/0017` 互补：0017 讲 Anthropic 凭证「如何注入」，本条讲所有 provider 密钥「如何不泄漏」。
