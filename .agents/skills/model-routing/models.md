# 模型映射

## Category

| Category | 模型 | Effort | Fallback |
|---|---|---|---|
| `quick` | `gpt-6.1-terra` | low | `gemini-4-hydrogen` · low → `claude-haiku-5-5` · medium |
| `unspecified-low` | `gpt-6.2-sol` | low | `gemini-4-neon` · low → `claude-sonnet-5-5` · medium |
| `unspecified-high` | `gemini-4-neon` | high | `claude-opus-5-5` · high → `gpt-6.2-sol` · high |
| `deep-low` | `gpt-6.2-sol` | medium | `gemini-4-neon` · medium → `claude-opus-5-5` · medium |
| `deep-high` | `gpt-6.2-astra` | high | `gemini-4-neon` · high → `claude-fable-5-2` · high |
| `ultrabrain` | `gpt-6.2-astra` | max | `claude-fable-5-2` · xhigh → `gemini-4-carbon` · high |
| `architect` | `claude-fable-5-2` | xhigh | `gpt-6.2-astra` · xhigh → `gemini-4-neon` · xhigh |
| `visual-engineering` | `gemini-4-neon` | high | `claude-opus-5-5` · high → `gpt-6.2-sol` · high |
| `artistry` | `claude-fable-5-2` | xhigh | `gemini-4-carbon` · high → `gpt-6.2-astra` · high |
| `writing` | `gemini-4-argon` | medium | `claude-opus-5-5` · medium → `gpt-6.2-sol` · medium |

## Review

| 实现者 Harness | Reviewer | Effort | Fallback |
|---|---|---|---|
| Claude Code | Codex · `gpt-6.2-astra` | high | Antigravity CLI · `gemini-4-neon` · high |
| Codex | Antigravity CLI · `gemini-4-neon` | high | Claude Code · `claude-opus-5-5` · high |
| Antigravity CLI | Codex · `gpt-6.2-astra` | high | Claude Code · `claude-opus-5-5` · high |
