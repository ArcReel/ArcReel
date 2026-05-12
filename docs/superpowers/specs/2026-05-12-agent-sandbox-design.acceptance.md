# Agent 沙箱化 合并前验收 Checklist

## 安全红线（必过）

- [ ] Bash 子进程不可见 provider 密钥
  - 步骤：启 session → agent 跑 `Bash("env | grep -E 'ANTHROPIC|ARK|XAI|GEMINI|VIDU'")`
  - 期望：输出为空 或 仅显示 ANTHROPIC_*（PoC #1 决定）
- [ ] agent 不能读 `.env`
  - agent 跑 `Bash("cat /app/.env")` → 输出含 violation
- [ ] agent 不能读 `.arcreel.db`
  - agent 跑 `Bash("cat /app/projects/.arcreel.db")` → violation
- [ ] agent 不能读 `vertex_keys/`
  - agent 跑 `Bash("ls /app/vertex_keys")` → violation
- [ ] agent 不能读 `agent_runtime_profile/.claude/settings.json`
  - agent 跑 `Bash("cat /app/agent_runtime_profile/.claude/settings.json")` → violation
- [ ] agent 不能写项目目录外
  - agent 跑 `Bash("touch /app/lib/x.txt")` → violation
- [ ] 父进程 `os.environ` 不含 provider 密钥
  - 启动 server 后 `python -c "import os; print([k for k in os.environ if 'KEY' in k or 'CRED' in k])"`
  - 期望：不含 ANTHROPIC_API_KEY/ARK_API_KEY 等

## 功能验收

- [ ] agent 在项目目录内自由跑 ls / cat / jq / python -c
- [ ] 新增 skill 脚本无需改权限配置
  - 临时加一个 echo skill，调用不报权限错
- [ ] agent 可自由 curl 任意域名
- [ ] 切换 Anthropic 配置后新 session 生效
  - 旧 session 仍用旧值；新建 session 用新值

## 平台覆盖

- [ ] macOS 本地：sandbox-exec 启用，PoC 全通
- [ ] Linux 本地（含 bwrap）：bwrap 启用，PoC 全通
- [ ] Docker：enableWeakerNestedSandbox 启用，PoC 全通
