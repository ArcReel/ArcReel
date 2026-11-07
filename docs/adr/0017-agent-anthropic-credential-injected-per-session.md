---
status: accepted
---

# Store Agent Anthropic credentials separately and inject DB-derived env per session, never global os.environ

Global env has one process-wide value, conflicting with multiple credentials and potentially different active credentials per session; provider secrets are already entirely forbidden in `os.environ`. Store Claude Agent SDK Anthropic gateway credentials in a separate table, distinct from custom-provider credentials, outside `ENDPOINT_REGISTRY`, and uninvolved in media generation. Whenever a new Agent session is created, `build_anthropic_env_dict` takes a DB session, reads the active credential, and returns a dict injected into `ClaudeAgentOptions.env`, **without writing global os.environ**. The activate endpoint only sets active and does not synchronize env.

## Consequences

- Running sessions retain their spawn-time env. Switching active affects only **new** sessions, with a toast but no forced termination.
- Complements `docs/adr/0008` (custom-provider credential modeling): both clarify the boundaries keeping credential storage independent of protocol form/process globals and separating runtime selection from startup declarations.
