# 模型映射

Claude 模型用 Claude Code，GPT 模型用 Codex，Gemini 模型用 Antigravity CLI（`agy`）。`agy` 的 `--model` 取 `<模型>-<effort>`，例如 `gemini-4-carbon-high`。

## Category

| Category | 模型 | Effort | Fallback |
|---|---|---|---|
| `quick` | `gemini-4-hydrogen` | medium | `claude-sonnet-5-5` · medium |
| `unspecified-low` | `claude-opus-5-5` | medium | `gemini-4-carbon` · medium |
| `unspecified-high` | `claude-opus-5-5` | high | `gemini-4-carbon` · high |
| `deep-low` | `gemini-4-carbon` | medium | `gpt-6.1-sol` · medium |
| `deep-high` | `gpt-6-astra` | high | `gemini-4-carbon` · high |
| `ultrabrain` | `gpt-6-astra` | max | `claude-fable-5-2` · max |
| `architect` | `claude-fable-5-2` | max | `claude-opus-5-5` · max |
| `visual-engineering` | `gemini-4-carbon` | high | `claude-opus-5-5` · high |
| `artistry` | `claude-fable-5-2` | max | `claude-opus-5-5` · max |
| `writing` | `gemini-4-argon` | low | `claude-opus-5-5` · low |

## Review

| 实现者 Harness | Reviewer | Effort | Fallback |
|---|---|---|---|
| Claude Code | Antigravity CLI · `gemini-4-carbon` | high | Codex · `gpt-6-astra` · high |
| Codex | Claude Code · `claude-opus-5-5` | high | Antigravity CLI · `gemini-4-carbon` · high |
| Antigravity CLI | Claude Code · `claude-opus-5-5` | high | Codex · `gpt-6-astra` · high |
