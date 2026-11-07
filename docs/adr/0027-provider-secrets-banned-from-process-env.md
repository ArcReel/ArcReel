---
status: accepted
---

> Windows Bash fallback now permits only `python .claude/skills/` entry points, no longer ffmpeg or ffprobe. Token-boundary matching remains a general rule; `ffmpegX` below is the historical example. The three rejection rules and three secret-enforcement layers remain valid.

# Keep provider secrets out of process env: startup assertions, Agent env overrides, and Bash env -u stripping

Provider secrets in `os.environ` are inherited wholesale by Agent subprocesses, broadening leakage; process-wide single values also conflict with multiple credentials and per-session switching. Store keys only in DB with three enforcement layers: (1) startup assertions fail fast if `os.environ` contains any real provider secret (`PROVIDER_SECRET_KEYS`), deliberately refusing env-key deployments; (2) inject Anthropic credentials per session from DB (see `docs/adr/0017`), overriding every other provider variable with an empty string; (3) PreToolUse wraps Bash commands in `env -u … sh -c`, dynamically unsetting sensitive inherited variables using the fixed list plus patterns such as `*_API_KEY`/`*_AUTH_TOKEN`.

## Consequences

- New providers register their environment-variable names in `lib/config/env_keys.py`, shared by startup assertions, env overrides, and Bash stripping. Pattern scans cover only conventionally named variables.
- Layer 3 is POSIX, requiring `env`/`sh`, and wraps commands only with sandboxing available on macOS/Linux. The hook rewrites commands without making permission decisions; later allowed_tools checks authorize them. Native Windows skips wrapping and sends raw commands to `can_use_tool`'s Bash allowlist with three rejection rules: token boundaries (blocking collisions such as `ffmpegX`), shell metacharacters (semicolon, `&`, pipes, angle brackets, backticks, `$`, newline, blocking chaining/pipes/redirection/substitution), and `..` traversal (including quoted `".."` and escaped `.\.` obfuscation, preventing arbitrary scripts outside skills). The `python` entry is further limited to `.claude/skills/<skill>/scripts/<script>.py`. Diagnostic rejection messages identify the matched rule. Layers 1 and 2 are platform-independent.
- Complements `docs/adr/0017`: that ADR explains how Anthropic credentials are injected; this one explains how all provider secrets are kept from leaking.
