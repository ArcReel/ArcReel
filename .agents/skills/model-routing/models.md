# Model mapping

Use Claude Code for Claude models and Codex for GPT models. Replace Fable 5.1 in the table when Fable 5.5 becomes generally available.

## Category

| Category | Model | Effort | Fallback |
|---|---|---|---|
| `quick` | `claude-sonnet-5-5` | medium | `gpt-6.1-terra` · medium |
| `unspecified-low` | `claude-opus-5-5` | medium | `gpt-6.1-sol` · medium |
| `unspecified-high` | `claude-opus-5-5` | high | `gpt-6-astra` · high |
| `deep-low` | `gpt-6.1-sol` | medium | `claude-opus-5-5` · high |
| `deep-high` | `gpt-6-astra` | high | `claude-fable-5-1` · high |
| `ultrabrain` | `gpt-6-astra` | max | `claude-fable-5-1` · max |
| `architect` | `claude-fable-5-1` | max | `gpt-6-astra` · max |
| `visual-engineering` | `claude-opus-5-5` | high | `gpt-6.1-sol` · high |
| `artistry` | `claude-fable-5-1` | max | `gpt-6-astra` · max |
| `writing` | `claude-opus-5-5` | low | `gpt-6.1-sol` · medium |

## Review

| Implementer Harness | Reviewer | Effort |
|---|---|---|
| Codex | Claude Code · `claude-opus-5-5` | high |
| Claude Code | Codex · `gpt-6-astra` | high |
