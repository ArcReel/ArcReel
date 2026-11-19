# 模型映射

按模型 ID 前缀选择 Harness。`gpt-*` 用 Codex，`claude-*` 用 Claude Code，`gemini-*` 用 Antigravity CLI（`agy`）。启动 `agy` 时，将模型 ID 和 effort 拼成 `--model` 的值，例如 Neon 的 `high` 对应 `--model gemini-4-neon-high`。

## Category

| Category | 模型 | Effort | Fallback |
|---|---|---|---|
| `quick` | `gpt-6.2-sol` | low | `gemini-4-hydrogen` · medium → `claude-sonnet-5-5` · medium |
| `unspecified-low` | `gpt-6.2-sol` | low | `gemini-4-hydrogen` · medium → `claude-sonnet-5-5` · high |
| `unspecified-high` | `gemini-4-neon` | high | `claude-opus-5-5` · high → `gpt-6.2-sol` · high |
| `deep-low` | `gpt-6.2-sol` | medium | `gemini-4-neon` · medium → `claude-opus-5-5` · high |
| `deep-high` | `gpt-6.2-astra` | high | `claude-fable-5-2` · xhigh → `gemini-4-neon` · xhigh |
| `ultrabrain` | `gpt-6.2-astra` | max | `claude-fable-5-2` · xhigh → `gemini-4-carbon` · high |
| `architect` | `claude-fable-5-2` | xhigh | `gpt-6.2-astra` · xhigh → `gemini-4-neon` · xhigh |
| `visual-engineering` | `gpt-6.2-sol` | max | `gemini-4-carbon` · high → `claude-sonnet-5-5` · xhigh |
| `artistry` | `claude-fable-5-2` | xhigh | `gemini-4-argon` · high → `gpt-6.2-astra` · high |
| `writing` | `gemini-4-argon` | high | `claude-opus-5-5` · medium → `gpt-6.2-sol` · medium |

## Review

| 实现者 Harness | Reviewer | Effort | Fallback |
|---|---|---|---|
| Claude Code | Antigravity CLI · `gemini-4-neon` | high | Codex · `gpt-6.2-astra` · high |
| Codex | Claude Code · `claude-opus-5-5` | high | Antigravity CLI · `gemini-4-neon` · high |
| Antigravity CLI | Claude Code · `claude-opus-5-5` | high | Codex · `gpt-6.2-astra` · high |
