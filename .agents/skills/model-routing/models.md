# 模型映射

Claude 模型用 Claude Code，GPT 模型用 Codex，Gemini 模型用 Antigravity CLI（`agy`）。`agy` 的 `--model` 取 `<模型>-<effort>`，例如 `gemini-4-carbon-high`。

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
