---
status: accepted
---

# The embedded Agent calls same-origin HTTP APIs with a short-lived JWT and open networking

Custom-endpoint adaptation requires the embedded Agent to read arbitrary provider documentation and call ArcReel's existing validate, endpoint-test,
and save HTTP APIs. The former fixed sandbox `allowedDomains` list cannot cover user-selected provider domains; wrapping these APIs again
as permanent MCP / SDK tools would create a second Agent surface.

Set the embedded Agent sandbox network configuration to `allowedDomains: ["*"]` with `allowLocalBinding: true`; retain file
`denyRead` / `denyWrite`, the ban on unsandboxed fallback, and the Windows command allowlist. Add `WebFetch` to allowed tools,
but not `WebSearch`. Each time session options are built, issue a 15-minute session JWT to
`ARCREEL_API_TOKEN` if authentication is enabled, and inject `ARCREEL_API_BASE` (default `http://127.0.0.1:1241/api/v1`); with authentication disabled,
the token is empty. Reserve this token name for sandbox skill scripts; continue stripping other provider / secret-like environment variables
from Bash subprocesses.

Both network switches must be explicit; neither can be omitted. `allowedDomains` is a preapproval list, not a restriction list: omitting the `network`
key gives zero preapprovals. Unattended sessions reject requests to approve new domains, cutting off outbound access instead. `allowLocalBinding` separately
controls loopback; neither `*` nor `127.0.0.1` in `allowedDomains` covers it. Skill scripts calling the same-origin
ArcReel API require loopback access.

This expands the trust boundary in two directions. First, prompt injection in provider documentation or a compromised Agent can exfiltrate the token
to any domain during its validity, and call every ArcReel API accessible to that administrator session JWT, including provider and
Agent credential configuration (responses mask secrets). Bash secret-like environment stripping therefore hides secret values only from the process
environment; it does not prevent an Agent holding the token from using saved credentials through APIs.
JWTs are stateless and cannot be revoked early. The only invalidation is rotating `AUTH_TOKEN_SECRET`, at the cost
of logging out every Web session. Fifteen minutes narrows time, not privileges. It is also the API availability
window within a session: tokens do not renew, so authenticated skill-script API calls fail with 401 after fifteen minutes and require a new
session for a fresh token. Second, `allowLocalBinding` exposes the entire
host loopback, not only ArcReel's port: local databases, other applications' development servers, and private local services
are reachable from sandbox Bash. File guards do not cover this route.

Accept these risks so the same HTTP API can serve both external and embedded Agents. Skills still ask before paid
trial runs or destructive overwrites, but these are Agent behavioral constraints, not security isolation.
