---
status: accepted
---

# Remote MCP trusts existing API Keys directly, without another fine-grained authorization layer

Remote MCP always requires and accepts only existing `arc-` API Keys. Even with WebUI authentication disabled, it rejects anonymous calls, session JWTs, and download tokens. In the current single-user product, an API Key remains a fully trusted high-privilege automation identity: do not add project allowlists, capability scopes, key-level task ownership, quotas, or separate confirmation credentials. Tools retain `DEFAULT_USER_ID`, and tasks record origin `mcp`.

Remote tools exclude provider, Agent, API Key, and system-credential configuration and project deletion, and return no secrets. This is MCP's creation-capability boundary, not the Key's security-permission boundary. The same Key retains broad existing REST API access and should go only to trusted external Agents. Design fine-grained permissions separately if needed. Tools continue sharing project validation, transactional write entries, revision concurrency checks, migration gates, and other existing server invariants.

Review, pricing tiers, and lossy-content actions keep the embedded Agent's confirmation experience: skills explain impacts and obtain consent; explicitly authorized full autonomy may continue. Servers retain existing confirmation parameters without verifying extra human credentials. Any valid Key may query/cancel any project's tasks. Task tools with `project` verify actual membership and retain existing global queues, concurrency admission, and cost records.
