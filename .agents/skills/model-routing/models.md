# 模型映射

委派出现异常时，按同 Category 或同行的 fallback 链依次重试。

## Category

| Category | 模型 | Effort | Fallback |
|---|---|---|---|
| `quick` | `gemini-4-hydrogen` | low | `gpt-6.1-terra` · low → `claude-sonnet-5-5` · low |
| `standard` | `gemini-4-neon` | medium | `gpt-6.2-astra` · medium → `claude-opus-5-5` · medium |
| `complex` | `gemini-4-neon` | high | `gpt-6.2-astra` · high → `claude-opus-5-5` · high |
| `debug-fix` | `gpt-6.2-astra` | medium | `gemini-4-neon` · high → `claude-opus-5-5` · high |
| `visual` | `gemini-4-neon` | high | `claude-opus-5-5` · high → `gpt-6.2-astra` · high |
| `writing` | `gemini-4-neon` | medium | `claude-opus-5-5` · medium → `gemini-4-hydrogen` · medium |
| `architect` | `claude-opus-5-5` | xhigh | `gemini-4-carbon` · high → `gpt-6.2-astra` · xhigh |

`debug-fix` 明确命中极难算法、底层性能或复杂并发死锁攻坚时，手动将首选升至 `gpt-6.2-astra` · xhigh（fallback 为 `gemini-4-carbon` · high → `claude-opus-5-5` · xhigh）。

## Review

| 实现者 Harness | Reviewer | Effort | Fallback |
|---|---|---|---|
| Claude Code | Codex · `gpt-6.2-astra` | high | Antigravity CLI · `gemini-4-neon` · high |
| Codex | Antigravity CLI · `gemini-4-neon` | high | Claude Code · `claude-opus-5-5` · high |
| Antigravity CLI | Codex · `gpt-6.2-astra` | high | Claude Code · `claude-opus-5-5` · high |
