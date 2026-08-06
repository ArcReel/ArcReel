# ArcReel Security Threat Model

**Last security review:** 2026-08-06

**Assessment baseline:** ArcReel commit `6fb9bb1ee9dc19cd45712b47220b3d2a3f1d8b98`

## 1. Purpose and interpretation

This document describes ArcReel's current security model, trust boundaries, attacker capabilities, existing controls, and severity rules. It is intentionally written as an **as-is model** rather than a target architecture or a patch history.

Current vulnerabilities, control gaps, validation tasks, and remediation acceptance criteria are maintained privately through GitHub Security Advisories. Keeping the stable public threat model separate from the changing private finding register reduces duplicate conclusions and avoids disclosing unresolved vulnerabilities before coordinated remediation.

ArcReel is currently a **single-operator administrative application**, not a multi-tenant SaaS platform. Authentication separates the trusted operator from unauthenticated callers. It does not provide meaningful role-based access control, tenant isolation, or least-privilege API scopes:

- `CurrentUserInfo.role` is always `admin`.
- A login JWT authorizes the complete administrative application surface, including API-key management.
- An `arc-` API key is a broad automation credential. It authorizes most business and configuration APIs but cannot create, list, or revoke API keys.
- Repositories generally operate on the default user.
- A stolen JWT is therefore treated as complete administrator compromise. A stolen API key is a high-impact compromise of the broad surface that key can access.

## 2. System overview

ArcReel is a self-hosted AI video production workspace with the following principal components:

- A FastAPI backend that exposes authenticated APIs, public/static routes, self-authenticating event/download routes, and serves the React SPA.
- A React frontend that stores its bearer token in browser `localStorage` and sends it in `Authorization: Bearer ...` headers for normal API calls.
- Project files stored beneath `app_data_dir()` and managed through project, asset, upload, archive, versioning, and generation services.
- A SQLAlchemy database, commonly SQLite, that stores configuration, provider credentials, API-key hashes, task state, usage records, and other application data.
- Third-party AI provider integrations using configured credentials and base URLs.
- Generation workers that submit provider jobs, poll status, download generated artifacts, persist state, and recover interrupted work.
- Native media processing through ffmpeg and ffprobe.
- A Claude SDK-based project assistant with sandbox policy, permission callbacks, project-bound file rules, network restrictions, and in-process MCP tools.
- Docker deployment with bind-mounted project data, logs, environment configuration, Vertex credentials, and Claude session data.

## 3. Deployment profiles and severity modifiers

Security severity depends materially on service reachability and deployment context.

### 3.1 Local-only profile

The service is bound to loopback or otherwise reachable only by the operator. Remote network likelihood is reduced, but malicious imports, prompt injection, compromised providers, browser same-origin attacks, parser denial of service, and local token theft remain relevant.

### 3.2 Private remote profile

The service is reachable over a LAN, VPN, private tunnel, or authenticated reverse proxy. Authentication, TLS, proxy logs, internal-network SSRF, shared workstations, and accidental exposure become material.

### 3.3 Internet-exposed profile

The service is reachable from the public Internet. This profile is not currently supported. Network-reachable findings are assessed at full severity. Authentication-disabled operation, anonymous project disclosure, active-content delivery, brute-force pressure, and misconfigured reverse proxies become high-priority concerns.

### 3.4 Shared or multi-user profile

This profile is not currently supported. If mutually untrusted people use the same ArcReel instance, cross-user project access, default-user repositories, shared administrative API keys, project enumeration, and the absence of RBAC become high or critical concerns. Such use requires a new authorization model and a revised threat model.

## 4. Sensitive assets

### 4.1 Credentials and authentication material

- Provider API keys, access keys, secret keys, custom-provider secrets, and Vertex JSON credential files.
- Anthropic and Claude agent credentials, SDK session data, and any secrets injected into agent execution.
- JWT bearer tokens, `arc-` API keys, download tokens, event-stream tokens, authentication passwords, and token-signing secrets.
- Database credentials, reverse-proxy credentials, and deployment secrets.

### 4.2 Project and user content

- Source manuscripts, scripts, prompts, project metadata, drafts, episode plans, and model-generated text.
- Character, scene, prop, product, reference-image, and reference-audio definitions.
- Generated images, videos, audio, thumbnails, project archives, and version history.
- Agent transcripts, MCP arguments, tool results, and task outputs.

### 4.3 Persistent system data

- `.arcreel.db`, external SQL databases, WAL/SHM files, backups, snapshots, and migrations.
- Provider configuration, custom endpoints, task records, usage records, API-key hashes, and system settings.
- Server logs, reverse-proxy logs, exception traces, and diagnostic artifacts.

### 4.4 Availability and financial assets

- CPU, memory, disk space, file descriptors, worker concurrency, native subprocess capacity, and network bandwidth.
- Provider quotas, rate limits, account balances, paid generation budgets, and externally running provider jobs.

### 4.5 Runtime and host assets

- The application process and container filesystem.
- Bind-mounted project, log, environment, Vertex, and Claude data.
- Host and private-network services reachable after an application, native-parser, sandbox, or container-boundary compromise.

## 5. Security objectives and invariants

ArcReel should preserve the following properties.

1. **Administrative authorization:** Only an authenticated administrator may read private project data or mutate projects, credentials, providers, tasks, generation state, API keys, agent configuration, and system settings, except for explicitly documented public or self-authenticating routes.
2. **Narrow public delivery:** Browser-native media delivery must expose only the intended media resource, not arbitrary files within the project root.
3. **No same-origin active content from untrusted files:** Public or same-origin project-file delivery must not execute attacker-controlled HTML, SVG, XML, JavaScript, or equivalent active content in the ArcReel origin.
4. **Path confinement:** Untrusted names, paths, archive members, references, and tool arguments must remain within their approved project or application roots.
5. **Agent isolation:** The assistant must not read sensitive files, cross project boundaries, write outside the active project, bypass protected-file workflows, execute unrestricted host commands, or silently fall back to unsandboxed execution.
6. **MCP confinement:** In-process MCP tools must remain closure-bound to the active project and validate every identifier, path, field, and state transition because they run outside the OS sandbox.
7. **Controlled outbound access:** Provider-controlled and operator-configured destinations must not silently provide access to loopback, cloud metadata, private networks, or other sensitive services unless the operator has explicitly enabled and accepted that behavior.
8. **Bounded untrusted processing:** Archives, uploads, provider responses, model output, and media parsing must have explicit limits for bytes, entries, memory, disk, CPU, concurrency, and execution time.
9. **Secret minimization:** Secrets must not be returned unmasked, written to public project files, inherited by unnecessary subprocesses, exposed to the agent, or included in routine logs.
10. **Bearer-token impact:** A stolen JWT is treated as complete administrator compromise. This currently includes a download JWT during its five-minute validity because the general JWT authentication path accepts it as an administrator bearer credential. A stolen API key is treated as a high-impact compromise of most business and configuration APIs, excluding API-key management.
11. **Authentication-disabled isolation:** `AUTH_ENABLED=false` is safe only when network reachability is independently constrained to a trusted local environment.

## 6. Threat actors and capabilities

### 6.1 Anonymous network attacker

An unauthenticated caller can reach public endpoints, submit login attempts, inspect bootstrap and health behavior, request public files, and exercise any route accidentally omitted from centralized authentication. On an Internet-exposed instance, these actions can be automated continuously.

### 6.2 Attacker with a stolen JWT or API key

A stolen JWT provides complete administrator access, including API-key management. A stolen `arc-` API key authorizes most project, provider, generation, task, agent, and system APIs, but the API-key management router explicitly requires JWT authentication. API keys otherwise have no scopes or RBAC boundaries that materially reduce their impact.

### 6.3 Malicious content author or project supplier

A malicious party may supply manuscripts, prompts, source documents, media, project ZIP archives, project JSON, scripts, filenames, resource identifiers, and links intended to trigger resource exhaustion, stored active content, parser defects, path confusion, misleading state, or prompt injection.

### 6.4 Prompt-injection adversary

Any content interpreted by the LLM may contain instructions designed to override operator intent. Sources include manuscripts, imported project data, model/provider responses, tool results, and generated text.

Prompt injection does not need to escape the sandbox to cause harm. It may abuse allowed capabilities to:

- Alter project state or metadata.
- Create misleading files or active content.
- Enqueue expensive generation.
- Delete, replace, or regenerate allowed assets.
- Disclose data that the current tool is legitimately permitted to read.
- Induce the operator to open an attacker-controlled resource or approve an unsafe action.

### 6.5 Malicious or compromised provider

A configured AI provider, custom endpoint, or upstream service may return malicious text, malformed responses, internal URLs, oversized artifacts, slow streams, invalid media, or parser-targeting content. Providers are trusted recipients of submitted prompts and media, but their responses remain untrusted input to ArcReel.

### 6.6 Operator or administrator

The operator controls environment variables, exposure, TLS, reverse proxies, provider definitions, credentials, base URLs, database selection, and Docker settings. Operator-selected endpoints are intentional administrative input, not anonymous attacker input. Findings must state whether exploitation requires an administrator, a stolen administrator token, or a compromised provider.

### 6.7 Supply-chain attacker

A compromised Python package, Node package, container image, SDK, ffmpeg build, package registry, CI workflow, or base image may affect build or runtime integrity. This is production relevant but distinct from the normal remote-attacker model.

## 7. Trust boundaries and principal data flows

| Boundary | Untrusted or sensitive data crossing it | Primary controls | Consequence of control failure |
|---|---|---|---|
| Browser → FastAPI API | Bearer tokens, project data, uploads, prompts, provider settings | Centralized FastAPI dependencies, Pydantic validation, route-specific checks | Unauthorized administrative actions, data modification, cost abuse |
| Browser → public/self-auth routes | Project paths, media paths, query tokens, download tokens | Public-route allowlists, path confinement, purpose-bound token verification | Anonymous disclosure, token leakage, same-origin active content |
| FastAPI → database | Secrets, hashes, configuration, task and usage state | ORM parameterization, API masking, DB permissions | Credential and state disclosure, persistence tampering |
| FastAPI → project filesystem | Names, paths, archives, generated files, agent writes | Name normalization, `safe_join`, project locks, atomic writes, schema validation | Traversal, cross-project access, overwrite, persistent malicious content |
| FastAPI → external providers | Credentials, prompts, media, base URLs, job IDs | Authentication, provider registry, configured endpoints, path-specific HTTP timeouts | SSRF, secret forwarding, malformed responses, unexpected cost |
| Provider → ArcReel download/parser pipeline | URLs, response headers, media bytes | Path-specific HTTP timeouts, artifact-path handling, downstream format checks; Vertex Gemini URI downloads currently lack an explicit deadline | SSRF, memory/disk exhaustion, parser compromise |
| Application → Claude SDK sandbox | LLM-selected tools, commands, paths, environment | `AgentAccessPolicy`, permission callbacks, deny rules, network policy | Sensitive-file access, cross-project access, command execution |
| Application → in-process MCP tools | LLM-selected structured arguments | Closure-bound project context, strict validation, protected workflows | Sandbox bypass through main-process capability |
| Application → ffmpeg/ffprobe | Uploaded or provider-supplied media | Extension checks, argument-list subprocess invocation, selected lifecycle cleanup | Native-parser exploitation, CPU/I/O starvation, stuck workers |
| Container → host/private network | Bind mounts, capabilities, network identity | Mount scope, host permissions, Agent sandbox, application access policy | Expanded blast radius after process or sandbox compromise |
| Reverse proxy/logging layer | Tokens, paths, query strings, forwarded headers | TLS, header policy, redaction, access-log configuration | Credential leakage, scheme confusion, unexpected exposure |

## 8. Current design assumptions and constraints

- ArcReel is operated by one trusted administrator.
- Authentication is enabled by default. Authentication-disabled operation is intended only for a trusted local environment.
- Remote deployments are expected to use TLS through a reverse proxy, VPN, or secure tunnel.
- Host permissions and database access controls protect application data, logs, credential files, and backups.
- The operator accepts that configured AI providers receive submitted prompts and media.
- Custom-provider endpoints are an intentional administrative capability with SSRF implications.
- Browser authentication uses explicit bearer tokens rather than authentication cookies. Traditional CSRF is therefore lower priority than XSS, token theft, URL leakage, and active same-origin content.
- Tokens stored in `localStorage` are readable by any JavaScript executing in the ArcReel origin.
- Docker is not assumed to provide a strong independent second sandbox under the current runtime configuration.
- Provider responses, model Markdown, project files, archive members, downloaded media, and LLM-selected tool arguments remain untrusted regardless of source.

## 9. Current security controls

### 9.1 Authentication and authorization

`server/auth.py` currently provides:

- Default-on authentication.
- Fail-closed handling for a blank `AUTH_ENABLED` value.
- Generated administrative passwords when none is configured.
- Seven-day HS256 JWTs.
- Five-minute project-bound download tokens. Export routes enforce their purpose and project binding, but general JWT authentication currently accepts them as administrator bearer credentials during their validity.
- Random `arc-` API keys stored as SHA-256 hashes.
- API-key expiration checks and bounded cache behavior.

There is no account-level RBAC, scoped API key, MFA, JWT revocation list, centralized session inventory, or built-in login throttling. JWTs authorize the complete administrative surface. API keys authorize most business and configuration APIs but are rejected by API-key management endpoints.

**Route authorization is defined centrally in `server/app.py`.** A security review must combine:

1. Endpoint dependencies.
2. `APIRouter` dependencies.
3. `app.include_router(..., dependencies=[Depends(get_current_user)])` dependencies.
4. Endpoint-internal token verification.

The absence of a `CurrentUser` parameter in a route function does not establish that the route is unauthenticated. The built-in `providers.router` is currently protected by the centralized registration dependency.

Public routes include authentication bootstrap/login, project/global file delivery, `/health`, and `/skill.md`. Self-authenticating routes include event streams that accept a query token and project export routes that verify a short-lived download token.

### 9.2 Secret handling

- API responses generally mask stored secrets.
- The server fails fast when provider secrets are present in the parent process environment, reducing automatic inheritance by sandboxed child processes.
- Agent policy denies sensitive-file reads and scrubs secret-like environment variables from sandboxed Bash execution.
- Vertex credential files are written with restrictive permissions where supported.

Built-in provider, custom-provider, and Agent credentials are nevertheless stored in plaintext database columns. API masking does not protect a copied database, backup, snapshot, or compromised database account.

### 9.3 Path and project controls

- Project names and asset names are normalized and validated.
- `safe_join` and `try_safe_join` provide root-containment checks.
- Project writes use locks, staged writes, atomic replacement, or rollback in important flows.
- Project schemas and imported data are validated and migrated.
- Media and source upload routes enforce supported extensions. Project imports and Vertex credential uploads validate content without requiring a matching original filename extension. Dedicated storyboard, shot-video, end-frame, and character reference-audio flows also enforce byte limits; general asset-image upload flows currently read the complete request without an explicit byte ceiling.

Path containment prevents escape from a root. It does not authorize anonymous access to every file inside that root.

### 9.4 Project archive controls

Project ZIP import currently rejects:

- Absolute paths.
- Drive-letter absolute paths.
- `..` traversal members.
- Symbolic-link entries.
- Encrypted entries.

Extraction occurs in a temporary staging directory. Imported project data is repaired, migrated, and validated before installation. Overwrite installation includes rollback behavior.

### 9.5 Agent controls

On supported Linux and macOS deployments, ArcReel verifies sandbox tooling at startup rather than silently running without it. `AgentAccessPolicy` centralizes:

- Current-project read and write boundaries.
- Cross-project read denial.
- Sensitive-file denial.
- Protected-write workflows.
- Sandbox filesystem deny rules.
- Sandbox network-domain policy.
- Secret-like environment scrubbing.
- Windows command-whitelist fallback behavior.
- Prevention of unsandboxed command fallback.

In-process MCP tools intentionally run outside the OS sandbox. Their project binding and validation are security-critical.

### 9.6 Frontend and browser controls

- React escaping reduces ordinary DOM injection risk.
- Selected URL helpers restrict login return paths and image protocols.
- The frontend attaches explicit bearer headers to normal API calls.
- The bearer token is stored in `localStorage`.
- Model-controlled Markdown is rendered through `streamdown` and requires behavior-specific validation for raw HTML and dangerous URLs.

### 9.7 CORS and logging

When `CORS_ORIGINS` is absent, empty, or contains `*`, ArcReel uses wildcard origins with credentials disabled. This does not independently bypass bearer authentication because an attacker-controlled origin does not know the token.

Application request logging records URL paths rather than complete query strings. Reverse proxies, ingress controllers, monitoring systems, and diagnostic middleware may still record query parameters.

## 10. Attack surfaces and abuse cases

### 10.1 Authentication and bearer tokens

- Automated login attempts may be sent without built-in rate limiting.
- A stolen JWT provides full administrative access; a stolen API key provides broad access except to API-key management.
- A leaked download token can be replayed and used as a general administrator bearer credential during its five-minute validity.
- Seven-day JWT lifetime increases the useful period of a stolen token.
- Event-stream routes may accept a full JWT or API key in a query parameter.
- `AUTH_ENABLED=false` causes authentication dependencies to return an anonymous administrator identity.

### 10.2 Public and self-authenticating routes

Browser-native `<img>`, `<video>`, EventSource, and download navigation create pressure to bypass normal authorization headers. Every public or self-authenticating route must therefore be reviewed as a separate authorization boundary, including:

- Allowed resource classes.
- Project and resource binding.
- Path containment.
- Token purpose and expiry.
- MIME inference and inline rendering.
- Cache behavior.
- Query-string logging.

### 10.3 Provider configuration and outbound requests

Authenticated administrators can configure supported provider URLs and custom endpoints. Providers can return media URLs and untrusted responses. Reviews must distinguish:

- Anonymous input.
- Administrator-supplied endpoints.
- Stolen-token control.
- Malicious or compromised provider responses.

Outbound requests must be assessed for private-address reachability, cloud metadata access, scheme handling, redirects, response limits, timeouts, and credential forwarding.

### 10.4 Imports, uploads, and project data

Malicious archives and uploads may target:

- Path traversal and symlink handling.
- File-count, path-depth, compression-ratio, JSON-size, memory, and disk exhaustion.
- Parser bugs in image, audio, video, document, or archive libraries.
- Project-schema confusion and unsafe repair behavior.
- Persistent prompt injection or active content.

Extension and declared-size checks do not establish that a file is safe for native parsing.

### 10.5 Agent runtime and prompt injection

Prompt injection may attempt to:

- Read credentials, logs, other projects, or host files.
- Modify protected project structures through alternate tools.
- Create executable or active content.
- Run commands or scripts outside the intended tool workflow.
- Use allowed MCP tools to mutate state or incur generation cost.
- Exploit differences between sandboxed SDK tools and unsandboxed in-process MCP tools.

A sandbox escape, permission-policy bypass, cross-project access, or in-process MCP validation defect is high impact. Authorized capability abuse remains relevant even when isolation controls operate as designed.

### 10.6 Media processing and workers

User-uploaded and provider-supplied media crosses into ffmpeg/ffprobe and worker execution. Relevant threats include:

- Native parser vulnerabilities.
- Long-running or stuck subprocesses.
- CPU, memory, disk, and I/O exhaustion.
- Worker starvation.
- Cancellation and cleanup failure.
- Provider jobs continuing to run and incur cost after local failure.

### 10.7 Frontend rendering and same-origin content

React escaping does not protect code paths that deliberately render Markdown, URLs, media, or arbitrary same-origin files. Any sanitizer bypass or active-content response may execute with access to `localStorage` and authenticated APIs.

CSP and browser security headers are defense in depth. They do not replace resource authorization, MIME restrictions, or output sanitization.

### 10.8 Deployment and container boundary

The current Docker deployment:

- Runs without an explicit non-root application user.
- Publishes port 1241.
- Disables Docker's default seccomp and AppArmor profiles for the application container.
- Adds `NET_ADMIN` for nested sandbox networking.
- Mounts environment configuration, projects, logs, Vertex keys, and Claude data.

These settings support nested bubblewrap operation but reduce Docker's strength as an independent containment layer. The Claude/bubblewrap sandbox and application policy are therefore primary controls.

## 11. Severity calibration

Severity combines impact, exploitability, authentication requirements, user interaction, and deployment reachability.

### 11.1 Critical

Use Critical for conditions such as:

- Unauthenticated administrator-equivalent access on a reachable deployment.
- Unauthenticated extraction, replacement, or exfiltration of provider or agent credentials.
- Arbitrary host/application-root file read or write, remote code execution, or sandbox escape with access to sensitive mounts.
- Reliable same-origin script execution that steals administrator credentials without meaningful user interaction.
- Unauthenticated SSRF to cloud metadata or sensitive internal services, especially where runtime identity or stored credentials are involved.

### 11.2 High

Use High for conditions such as:

- Anonymous disclosure of complete private project content on a remotely reachable deployment.
- Plausible prompt-injection or imported-content chains that produce same-origin active content and require operator interaction.
- Authenticated or stolen-token traversal outside the active project.
- Cross-project read/write or bypass of protected agent workflows.
- Administrative/custom-provider SSRF in a cloud or sensitive private network.
- Provider-controlled responses capable of exhausting the application process or host.
- Severe archive or parser exhaustion in a shared or untrusted-upload deployment.

### 11.3 Medium

Use Medium for conditions such as:

- Missing login rate limits in a remote deployment.
- JWT or API-key exposure through query-string logging.
- Authenticated ZIP-bomb or media-parser denial of service in the single-admin model.
- Plaintext provider secrets where database compromise is a separate prerequisite.
- Missing CSP or browser hardening that increases the impact of another flaw.
- Deployment weaknesses that require a material operator mistake but do not independently bypass bearer authentication.

### 11.4 Low

Use Low for conditions such as:

- Health, authentication-status, or version information disclosure without a useful exploit chain.
- Masked configuration metadata.
- Wildcard CORS with credentials disabled where protected APIs still require an unknown bearer token.
- Cosmetic redirect behavior constrained to local paths.
- Generic CSRF claims that require cookie authentication when the application uses explicit bearer tokens.
- Multi-tenant authorization concerns in a strictly single-operator deployment, unless mutually untrusted users are actually present.

## 12. Out of scope, accepted trust, and non-findings

- ArcReel does not currently claim adversarial multi-user or tenant isolation. This limitation must be revisited if the product adds users, teams, sharing, or roles.
- Configured AI providers are trusted recipients of submitted prompts and media. Malicious responses, URLs, and artifacts remain in scope.
- Operator-configured custom endpoints are intentional administrative functionality. They must not be described as anonymously attacker-controlled without an authentication bypass or stolen-token path.
- Built-in provider routes are centrally authenticated. The absence of `CurrentUser` in individual route signatures is not evidence of missing authentication.
- SQL injection requires a concrete unsafe query-construction path; generic SQLAlchemy usage is not a finding.
- Traditional CSRF is lower priority while state-changing APIs require explicit bearer tokens rather than browser cookies.
- Path traversal and authorization are separate questions. A safely contained path may still be disclosed to an unauthorized caller.
- Authentication-disabled mode is accepted only for independently isolated local use.

## 13. Security review rules

1. **Resolve actual route protection.** Inspect endpoint dependencies, router dependencies, centralized `include_router` dependencies, and endpoint-internal token verification before reporting missing authentication.
2. **Label evidence quality.** Use `Confirmed`, `Conditional`, `Needs validation`, or `Architectural risk`. Do not present an unverified library behavior as a confirmed vulnerability.
3. **Name the attacker and prerequisite.** Distinguish anonymous callers, stolen-token attackers, malicious imports, prompt injection, malicious providers, and trusted administrators.
4. **Apply deployment modifiers.** State whether severity assumes loopback-only, private remote, Internet exposure, cloud metadata reachability, or unsupported multi-user use.
5. **Trace the complete data flow.** Include authentication, normalization, validation, storage, redirects, MIME handling, user interaction, and downstream consumers.
6. **Do not equate containment with authorization.** `safe_join` may prevent `../` while still allowing unauthorized access to a sensitive file inside the root.
7. **Treat active formats as browser code.** Review MIME inference, `Content-Disposition`, `nosniff`, CSP, HTML/SVG/XML handling, and browser-readable token storage.
8. **Review the full outbound-fetch surface.** Include schemes, DNS resolution, IPv4/IPv6 special ranges, loopback, link-local, metadata addresses, redirects, allowlists, timeouts, `Content-Length`, actual byte ceilings, and streaming behavior.
9. **Review resource limits independently of traversal.** Archive entry count, compressed size, total expanded size, compression ratio, JSON size, path depth, subprocess deadline, concurrency, and cleanup are distinct controls.
10. **Review both agent boundaries.** Sandbox policy protects SDK tools and Bash; in-process MCP tools execute in the main process and require independent validation.
11. **Consider authorized capability abuse.** Prompt injection can be security-relevant without RCE when allowed tools can alter project state, incur cost, create active content, or mislead the operator.
12. **Avoid duplicate or inflated findings.** Prefer the most specific root cause and describe its consequences rather than reporting architectural context as multiple vulnerabilities.
13. **Require concrete bypasses for generic checklist claims.** CORS, CSRF, SQL injection, and masking reports require an actual exploit path in ArcReel.
14. **Use the private finding register.** Do not re-report an unchanged known gap as a new issue unless reachability, impact, or bypass conditions have materially changed.

## 14. Reassessment triggers

Rebuild or materially revise this threat model when ArcReel:

- Adds real users, invitations, roles, teams, public sharing, or tenant-specific data.
- Introduces cookie authentication, OAuth/OIDC, refresh tokens, or browser session cookies.
- Changes centralized route registration or adds public/self-authenticating routers.
- Adds public project sharing, signed media URLs, external embeds, or new downloadable file types.
- Changes agent tools, sandbox settings, network policy, MCP capabilities, protected-write rules, or Windows fallback behavior.
- Adds a provider protocol, custom endpoint type, upload path, webhook, URL-fetching feature, or provider-returned artifact type.
- Changes archive structure, import/export behavior, accepted source formats, or media parsers.
- Moves credentials to a different store, adds encryption, changes database deployment, or introduces backup automation.
- Changes Docker users, capabilities, security profiles, bind mounts, or server/agent process separation.
- Makes Internet exposure a first-class supported deployment mode.

## 15. Private risk register

Confirmed gaps, conditional attack chains, architectural risk amplifiers, validation tasks, priorities, and remediation acceptance criteria are maintained privately through GitHub Security Advisories. Public disclosure follows the coordinated process in the repository [security policy](../../SECURITY.md).
