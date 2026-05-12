# Agent 沙箱化 PoC 验证报告

> 日期：2026-05-12
> 平台：macOS 26 / Linux ubuntu-24 / Docker (Debian Trixie)
> SDK 版本：claude-agent-sdk-python 0.1.80
> 状态：**执行延后到 Task 4.3 完成后**

## 执行策略调整

PoC #1（自动化）与 PoC #2-#6（手动）均依赖：
1. 真实的 Anthropic API key（用户配置后才有）
2. ArcReel 沙箱已部分启用（Task 4.3 后才有）
3. 部分场景需要在 Docker 容器内执行（生产部署后才有）

为了不阻塞 Task 1-3 的纯净 Python 重构，本 PoC 改为**实施后验证**：

- **PoC #1 结果对设计不构成 gate**。spec 设计本身已采取防御立场（Task 4.3 注入空字符串覆盖 `options.env` 中所有 provider 密钥），无论 env 是否被 Bash 子进程继承，安全红线都成立。
- **PoC #2-#6 实质上是 Task 6 集成验收的子集**。Task 6.1 的 `test_sandbox_e2e.py` 自动化覆盖 PoC #2/#3/#5；Task 6.2 的手动 checklist 覆盖 PoC #4/#6。

## 后续动作

- Task 6.1 完成后回填本报告 PoC #1/#2/#3/#5 实测结果
- Task 6.2 完成后回填 PoC #4/#6 实测结果
- 如 PoC #1 阳性（env 泄漏），spec/plan 增补一个 PreToolUse Bash hook 从 `tool_input["env"]` 剥离 `PROVIDER_SECRET_KEYS` — 但请注意 Bash 工具不通过 `tool_input.env` 传 env，所以即使阳性其实也不需要 hook，只需依靠 Task 4.3 的空值覆盖

## 结果汇总（待填）

| # | 平台 | 期望 | 实际 | 通过 |
|---|---|---|---|---|
| 1 | macOS | options.env NOT inherited by bash | 待填 | ⏳ |
| 1 | Linux | options.env NOT inherited by bash | 待填 | ⏳ |
| 2 | macOS | cat .env denied | 待填 | ⏳ |
| 2 | Linux | cat .env denied | 待填 | ⏳ |
| 3 | macOS | ls/jq/python -c allowed | 待填 | ⏳ |
| 3 | Linux | ls/jq/python -c allowed | 待填 | ⏳ |
| 4 | macOS | curl example.com works | 待填 | ⏳ |
| 5 | macOS | echo > /app/lib/test.py denied | 待填 | ⏳ |
| 5 | Linux | echo > /app/lib/test.py denied | 待填 | ⏳ |
| 6 | Docker | enableWeakerNestedSandbox + bwrap reduced mode | 待填 | ⏳ |
