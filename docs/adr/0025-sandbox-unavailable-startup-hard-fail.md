---
status: accepted
---

# Fail server startup when the kernel sandbox is unavailable; only Windows has a documented fallback

Agent-tool security rests on kernel sandboxing (macOS Seatbelt / Linux bwrap). Silently degrading after failed detection removes filesystem and outbound-network defenses without notice. Check sandbox availability at server startup (`check_sandbox_available`): missing `sandbox-exec` on macOS, missing `bwrap`/`socat` on Linux, or an installed bwrap that fails its probe raises immediately and refuses to start the entire service (fail closed), rather than degrading or waiting until session creation. Only natively unsandboxed Windows is exempt: log a warning, disable sandboxing, and route Agent Bash through a code-level prefix allowlist.

## Consequences

- Install sandbox dependencies before deployment (macOS includes `sandbox-exec`; Linux needs `bwrap` + `socat`), or the service cannot start. This is deliberate: refuse service rather than run an unsandboxed Agent.
- Windows prefix checks are coarser than sandboxing and permit only limited command prefixes. WSL2/Docker remains recommended for production to use full sandboxing.
