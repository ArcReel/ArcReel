# ArcReel Security Threat Model

The [security policy](../../SECURITY.md) defines supported deployments, versions, and private reporting. ArcReel is a self-hosted administrative application for one trusted operator. Authentication separates that operator from unauthenticated callers. Mutually untrusted users require a different authorization model.

This document records trust boundaries, existing controls, and how to assess a change. The historical security assessment ran on 2026-08-06 at commit `6fb9bb1ee9dc19cd45712b47220b3d2a3f1d8b98`. Control descriptions follow the implementation. Verified gaps and remediation details belong in private GitHub Security Advisories.

## Deployment profiles and severity modifiers

Security severity depends materially on service reachability and deployment context.

### Local-only profile

The service is bound to loopback or otherwise reachable only by the operator. Remote network likelihood is reduced, but malicious imports, prompt injection, compromised providers, browser same-origin attacks, parser denial of service, and local token theft remain relevant.

### Private remote profile

The service is reachable over a LAN, VPN, private tunnel, or authenticated reverse proxy. Authentication, TLS, proxy logs, internal-network SSRF, shared workstations, and accidental exposure become material.

### Internet-exposed profile

The service is reachable from the public Internet. This profile is not currently supported. Network-reachable findings are assessed at full severity. Authentication-disabled operation, anonymous project disclosure, active-content delivery, brute-force pressure, and misconfigured reverse proxies become high-priority concerns.

### Shared or multi-user profile

This profile is not currently supported. If mutually untrusted people use the same ArcReel instance, cross-user project access, default-user repositories, shared administrative API keys, project enumeration, and the absence of RBAC become high or critical concerns. Such use requires a new authorization model and a revised threat model.

## Sensitive assets

### Credentials and authentication material

- Provider API keys, access keys, secret keys, custom-provider secrets, and Vertex JSON credential files.
- Anthropic and Claude agent credentials, SDK session data, and any secrets injected into agent execution.
- JWT bearer tokens, `arc-` API keys, download tokens, authentication passwords, and token-signing secrets.
- Database, object-storage, and reverse-proxy credentials, signed object URLs, and deployment secrets.

### Project and user content

- Source manuscripts, scripts, prompts, project metadata, drafts, episode plans, and model-generated text.
- Character, scene, prop, product, reference-image, and reference-audio definitions.
- Generated images, videos, audio, thumbnails, project archives, and version history.
- Agent transcripts, MCP arguments, tool results, task outputs, and image content stored in an object bucket.

### Persistent system data

- `arcreel.db`, external SQL databases, WAL/SHM files, backups, snapshots, and migrations.
- Provider configuration, custom endpoints, task records, usage records, API-key hashes, and system settings.
- Server logs, reverse-proxy logs, exception traces, and diagnostic artifacts.

### Availability and financial assets

- CPU, memory, disk space, file descriptors, worker concurrency, native subprocess capacity, and network bandwidth.
- Provider quotas, rate limits, account balances, paid generation budgets, and externally running provider jobs.

### Runtime and host assets

- The application process and container filesystem.
- Bind-mounted project, log, environment, Vertex, and Claude data.
- Host and private-network services reachable after an application, native-parser, sandbox, or container-boundary compromise.

## Threat actors and capabilities

### Anonymous network attacker

An unauthenticated caller can reach public endpoints, submit login attempts, inspect bootstrap and health behavior, request public files, and exercise any route accidentally omitted from centralized authentication. On an Internet-exposed instance, these actions can be automated continuously.

### Attacker with a stolen JWT or API key

A stolen login JWT or API key provides broad administrative access. Credential responses mask stored values, but both credential classes can change provider and Agent configuration. See [Authentication and authorization](#authentication-and-authorization) for the API-key management exception and [Secret handling](#secret-handling) for secret storage.

### Malicious content author or project supplier

A malicious party may supply manuscripts, prompts, source documents, media, project ZIP archives, project JSON, scripts, filenames, resource identifiers, and links intended to trigger resource exhaustion, stored active content, parser defects, path confusion, misleading state, or prompt injection.

### Prompt-injection adversary

Any content interpreted by the LLM may contain instructions designed to override operator intent. Sources include manuscripts, imported project data, model/provider responses, tool results, and generated text.

Prompt injection does not need to escape the sandbox to cause harm. It may abuse allowed capabilities to:

- Alter project state or metadata.
- Create misleading files or active content.
- Enqueue expensive generation.
- Delete, replace, or regenerate allowed assets.
- Disclose data that the current tool is legitimately permitted to read.
- Induce the operator to open an attacker-controlled resource or approve an unsafe action.

### Malicious or compromised provider

A configured AI provider, custom endpoint, or upstream service may return malicious text, malformed responses, internal URLs, oversized artifacts, slow streams, invalid media, or parser-targeting content. Providers are trusted recipients of submitted prompts and media, but their responses remain untrusted input to ArcReel.

### Operator or administrator

The operator controls environment variables, exposure, TLS, reverse proxies, provider definitions, credentials, base URLs, database selection, and Docker settings. Operator-selected endpoints are intentional administrative input, not anonymous attacker input. Findings must state whether exploitation requires an administrator, a stolen administrator token, or a compromised provider.

### Supply-chain attacker

A compromised Python package, Node package, container image, SDK, ffmpeg build, package registry, CI workflow, or base image may affect build or runtime integrity. This is production relevant but distinct from the normal remote-attacker model.

## Trust boundaries and principal data flows

| Boundary | Untrusted or sensitive data crossing it | Primary controls | Consequence of control failure |
|---|---|---|---|
| Browser → FastAPI API | Bearer tokens, project data, uploads, prompts, provider settings | Centralized FastAPI dependencies, Pydantic validation, route-specific checks | Unauthorized administrative actions, data modification, cost abuse |
| Browser → public/self-auth routes | Project paths, media paths, download tokens | Public-route allowlists, path confinement, purpose-bound token verification | Anonymous disclosure, token leakage, same-origin active content |
| FastAPI → database | Secrets, hashes, configuration, task and usage state | ORM parameterization, API masking, DB permissions | Credential and state disclosure, persistence tampering |
| FastAPI → project filesystem | Names, paths, archives, generated files, agent writes | Name normalization, `safe_join`, project locks, atomic writes, schema validation | Traversal, cross-project access, overwrite, persistent malicious content |
| FastAPI → external providers | Credentials, prompts, media, base URLs, job IDs | Authentication, provider registry, configured endpoints, path-specific HTTP timeouts | SSRF, secret forwarding, malformed responses, unexpected cost |
| Provider → ArcReel download/parser pipeline | URLs, response headers, media bytes | Shared guarded HTTP client where used, path-specific download budgets and limits, staged writes, downstream format checks | SSRF, memory/disk exhaustion, parser compromise |
| ArcReel → object storage → provider | Object-storage credentials, image bytes, scoped signed object URLs | Connection-bound object references, content digest checks on retrieval, provider access scoped to the signed object URL | Private image disclosure, lost or corrupted image data, misuse of storage credentials |
| Application → SDK built-in file tools | LLM-selected `Read`, `Write`, `Edit`, `Glob`, and `Grep` paths | `PreToolUse` hooks backed by `AgentAccessPolicy` | Sensitive-file access, cross-project access, protected-file modification |
| Application → sandboxed Bash | Commands, paths, environment, and network destinations | Kernel sandbox profile, `AgentAccessPolicy`, command policy, environment scrubbing | Sensitive-file access, cross-project access, command execution, network abuse |
| Application → in-process MCP tools | LLM-selected structured arguments | Closure-bound project context, strict validation, protected workflows | Sandbox bypass through main-process capability |
| Application → bundled ffmpeg | Uploaded or provider-supplied media | Extension checks, argument-list subprocess invocation, `file`-only protocol whitelist on media probes, subprocess deadlines, selected lifecycle cleanup | Native-parser exploitation, CPU/I/O starvation, stuck workers |
| Container → host/private network | Bind mounts, capabilities, network identity | Mount scope, host permissions, Agent sandbox, application access policy | Expanded blast radius after process or sandbox compromise |
| Reverse proxy/logging layer | Tokens, paths, query strings, forwarded headers | TLS, header policy, redaction, access-log configuration | Credential leakage, scheme confusion, unexpected exposure |

## Controls by trust boundary

### Authentication and authorization

`server/auth.py` currently provides:

- Default-on authentication.
- Fail-closed handling for a blank `AUTH_ENABLED` value.
- Generated administrative passwords when none is configured.
- Seven-day HS256 JWTs.
- Five-minute project-bound download tokens. Export routes enforce their purpose and project binding, and general session authentication rejects any JWT carrying a `purpose` claim, so they are not accepted as bearer credentials on other routes. Their subjects are copied from the caller, so tokens minted through API keys retain the `apikey:` prefix and remain excluded from API-key management.
- Random `arc-` API keys stored as SHA-256 hashes.
- API-key expiration checks and bounded cache behavior.

There is no account-level RBAC, scoped API key, MFA, JWT revocation list, centralized session inventory, or built-in login throttling. JWTs normally authorize the complete administrative surface. API keys authorize most business and configuration APIs but are rejected by API-key management endpoints. That distinction currently relies on `CurrentUserInfo.sub.startswith("apikey:")`, not on an explicit credential-type claim: because login JWT subjects copy `AUTH_USERNAME`, a configured username beginning with `apikey:` is also rejected by those endpoints.

**Route authorization is defined centrally in `server/app.py`.** A security review must combine:

1. Endpoint dependencies.
2. `APIRouter` dependencies.
3. `app.include_router(..., dependencies=[Depends(get_current_user)])` dependencies.
4. Endpoint-internal token verification.

The absence of a `CurrentUser` parameter in a route function does not establish that the route is unauthenticated. The built-in `providers.router` is currently protected by the centralized registration dependency.

Public routes include authentication bootstrap/login, project/global media delivery, `/health`, and `/agent-installation-guide.md`. Public media requests must resolve inside the allowed root. Files within that root return the same 404 whether absent or outside the media allowlist. Project files also need an allowed directory or the project-root style reference image. The media response carries `X-Content-Type-Options: nosniff`. See [ADR 0071](../adr/0071-browser-native-request-credential-carrier.md). Self-authenticating routes are limited to the project export routes that verify a short-lived download token; event streams are ordinary protected routes consumed by the frontend through `fetch` with an `Authorization` header as documented in the same ADR.

### Remote MCP transport

The remote MCP endpoint is mounted at `/mcp` and enforces an `arc-` API key on every request through `ArcApiKeyVerifier`. `AUTH_ENABLED=false` does not grant it anonymous access, and login JWTs and download tokens are rejected.

The MCP SDK's DNS-rebinding protection is disabled (`TransportSecuritySettings(enable_dns_rebinding_protection=False)`), so ArcReel validates neither the `Host` nor the `Origin` header inside the MCP mount. A rebound request reaches the endpoint but carries no credential, because the API key is never held in a browser cookie or session, and receives 401. Host ownership is delegated to the reverse proxy and deployment topology. Cross-origin protection for browser MCP clients rests entirely on the application-level CORS allowlist described under [Browser, origins, and logging](#browser-origins-and-logging). Disabling the flag does not affect the SDK's `Content-Type` validation for POST requests.

The endpoint is not an OAuth resource server: it publishes no RFC 9728 protected-resource metadata, and its 401 response is a plain `Bearer` challenge without `resource_metadata`, so discovery-based clients are never sent into an OAuth flow that has no authorization server behind it. The `issuer_url` required by the SDK's `AuthSettings` is a placeholder that is never published.

### Secret handling

- API responses mask stored secrets. No route returns a stored provider or Agent `api_key` in plaintext. Creating an Agent credential from a custom provider (`from_custom_provider_id` on `POST /api/v1/agent/credentials`) copies that provider's key server-side; the key never passes through the client. The new credential uses the base URL given in the request, or the provider's base URL when none is given.
- The server fails fast when provider secrets are present in the parent process environment, reducing automatic inheritance by sandboxed child processes.
- Agent policy denies sensitive-file reads and scrubs provider and secret-like environment variables from sandboxed
  Bash execution. The dedicated short-lived `ARCREEL_API_TOKEN` is intentionally retained so the embedded Agent can
  call ArcReel's HTTP API. The token carries administrator authority over provider and Agent credential
  configuration, so the scrubbing hides secret values from the sandboxed process but does not bound what an Agent
  holding the token can do with the stored credentials through the API.
- Vertex credential files are written with restrictive permissions where supported.

The database holds built-in provider, custom-provider, Agent, and object-storage credentials. Access to the database or its backups must be treated as access to stored credentials. API masking protects only response payloads.

### Project files, uploads, and archives

- Project names and asset names are normalized and validated.
- `safe_join` and `try_safe_join` provide root-containment checks.
- Project writes use locks, staged writes, atomic replacement, or rollback in important flows.
- Project schemas and imported data are validated and migrated.
- Media and source upload routes enforce supported extensions. Project imports and Vertex credential uploads validate content without requiring a matching original filename extension. Dedicated storyboard, shot-video, end-frame, and character reference-audio flows also enforce byte limits; general asset-image upload flows currently read the complete request without an explicit byte ceiling.

Public file routes apply media allowlists after path resolution, including when they retrieve a missing local working file from object storage. Remote content references and working files are defined in [ADR 0116](../adr/0116-remote-content-versions-saved-by-formal-commit-with-object-references.md).

Project ZIP import rejects these archive members:

- Absolute paths.
- Drive-letter absolute paths.
- `..` traversal members.
- Symbolic-link entries.
- Encrypted entries.

Extraction occurs in a temporary staging directory. Imported project data is repaired, migrated, and validated before installation. Overwrite installation includes rollback behavior. Archives carry content rather than live object-storage locations or credentials. See [ADR 0119](../adr/0119-archives-carry-content-not-storage-locators.md).

### Agent controls

On supported Linux and macOS deployments, ArcReel verifies sandbox tooling at startup rather than silently running without it. `AgentAccessPolicy` centralizes rules that are projected into both application-level SDK hooks and the kernel sandbox:

- Current-project read and write boundaries.
- Cross-project read denial.
- Sensitive-file denial.
- Protected-write workflows.
- Sandbox filesystem deny rules.
- Unrestricted sandbox outbound network for provider documentation and custom-endpoint adaptation: the domain
  allowlist is the single wildcard `*`, and `allowLocalBinding` additionally opens host loopback.
- Secret-like environment scrubbing. This is not a boundary against an Agent that holds `ARCREEL_API_TOKEN`; see [Secret handling](#secret-handling).
- Windows command-whitelist fallback behavior.
- Prevention of unsandboxed command fallback.

SDK built-in `Read`, `Write`, `Edit`, `Glob`, and `Grep` tools execute outside the Bash subprocess tree and are constrained by `PreToolUse` hooks, not by the kernel sandbox. Bash and its descendants are constrained by the kernel sandbox on supported platforms. In-process MCP tools also run outside the OS sandbox and require independent project binding and argument validation.

When authentication is enabled, the embedded Agent receives a 15-minute administrator session JWT. Its network policy permits outbound requests to arbitrary domains and access to host loopback services. Filesystem confinement does not contain prompt injection through these channels. Signing-key rotation invalidates an outstanding JWT and all web sessions. The privileges, lifetime, and accepted exposure are defined in [ADR 0069](../adr/0069-embedded-agent-http-api-access.md).

### Provider configuration and outbound requests

Authenticated administrators can configure supported provider URLs and custom endpoints. Providers can return media URLs and untrusted responses. Reviews must distinguish:

- Anonymous input.
- Administrator-supplied endpoints.
- Stolen-token control.
- Malicious or compromised provider responses.

Outbound requests must be assessed for private-address reachability, cloud metadata access, scheme handling, redirects, response limits, timeouts, and credential forwarding.

[The guarded artifact HTTP client](../../lib/backends/artifact_download_guard.py) checks schemes and metadata destinations at each hop, limits success and error bodies, and allows private-network connections needed by self-hosted providers. A pre-request DNS check does not pin the eventual connection. The shared client also covers submit, poll, and discovery paths where the endpoint or provider calls it directly. SDK calls and some dedicated probes use separate clients, so a review must verify which checks apply to the actual transport. Gemini video downloads, for example, have a separate wall-clock deadline and I/O timeouts in [the Gemini backend](../../lib/backends/video_backends/gemini.py).

Credential-bearing redirects use the scoped request helper where configured. Crossing origins drops rendered credentials and the request body. Media limits and temporary-file cleanup depend on the download entry point. See [the backend runtime](../../lib/backends/backend_runtime.py) and the backend in use.

### Browser, origins, and logging

- React escaping reduces ordinary DOM injection risk.
- Selected URL helpers restrict login return paths and image protocols.
- The frontend attaches explicit bearer headers to normal API calls.
- The bearer token is stored in `localStorage`.
- Model-controlled Markdown passes through `streamdown`. Validate raw HTML, dangerous URLs, and same-origin responses at each rendering entry point. Browser security headers provide a secondary defense. JavaScript executing in the ArcReel origin can access the bearer token in `localStorage`.

When `CORS_ORIGINS` is absent, empty, or contains `*`, ArcReel uses wildcard origins with credentials disabled. This does not independently bypass bearer authentication because an attacker-controlled origin does not know the token. The allowlist is parsed once in `server/cors_config.py` and applied by a single application-level `CORSMiddleware` that also wraps the `/mcp` mount; there is no MCP-specific origin list.

Application request logging records URL paths rather than complete query strings. Reverse proxies, ingress controllers, monitoring systems, and diagnostic middleware may still record query parameters.

### Media processing and workers

User-uploaded and provider-supplied media crosses into the bundled ffmpeg and worker execution. ArcReel invokes ffmpeg through an argument list and applies subprocess deadlines. Media probes limit ffmpeg to the `file` protocol. Extension and declared-size checks alone do not establish parser safety. Relevant threats include:

- Native parser vulnerabilities.
- Long-running or stuck subprocesses.
- CPU, memory, disk, and I/O exhaustion.
- Worker starvation.
- Cancellation and cleanup failure.
- Provider jobs continuing to run and incur cost after local failure.

### Deployment and container boundary

The current Docker deployment:

- Runs without an explicit non-root application user.
- Publishes port 1241.
- Disables Docker's default seccomp and AppArmor profiles for the application container.
- Adds `NET_ADMIN` for nested sandbox networking.
- Mounts the environment file, the data root, and Claude data. Logs and Vertex keys are part of the data root. See the [default](../../deploy/docker-compose.yml) and [PostgreSQL](../../deploy/production/docker-compose.yml) deployment definitions.

These settings support nested bubblewrap operation but reduce Docker's strength as an independent containment layer. The Claude/bubblewrap sandbox and application policy are therefore primary controls.

## Severity calibration

Severity combines impact, exploitability, authentication requirements, user interaction, and deployment reachability.

### Critical

Use Critical for conditions such as:

- Unauthenticated administrator-equivalent access on a reachable deployment.
- Unauthenticated extraction, replacement, or exfiltration of provider or agent credentials.
- Arbitrary host/application-root file read or write, remote code execution, or sandbox escape with access to sensitive mounts.
- Reliable same-origin script execution that steals administrator credentials without meaningful user interaction.
- Unauthenticated SSRF to cloud metadata or sensitive internal services, especially where runtime identity or stored credentials are involved.

### High

Use High for conditions such as:

- Anonymous disclosure of complete private project content on a remotely reachable deployment.
- Plausible prompt-injection or imported-content chains that produce same-origin active content and require operator interaction.
- Authenticated or stolen-token traversal outside the active project.
- Cross-project read/write or bypass of protected agent workflows.
- Administrative/custom-provider SSRF in a cloud or sensitive private network.
- Provider-controlled responses capable of exhausting the application process or host.
- Severe archive or parser exhaustion in a shared or untrusted-upload deployment.

### Medium

Use Medium for conditions such as:

- Missing login rate limits in a remote deployment.
- JWT or API-key exposure through query-string logging.
- Authenticated ZIP-bomb or media-parser denial of service in the single-admin model.
- Plaintext provider secrets where database compromise is a separate prerequisite.
- Missing CSP or browser hardening that increases the impact of another flaw.
- Deployment weaknesses that require a material operator mistake but do not independently bypass bearer authentication.

### Low

Use Low for conditions such as:

- Health, authentication-status, or version information disclosure without a useful exploit chain.
- Masked configuration metadata.
- Wildcard CORS with credentials disabled where protected APIs still require an unknown bearer token.
- Cosmetic redirect behavior constrained to local paths.
- Generic CSRF claims that require cookie authentication when the application uses explicit bearer tokens.
- Multi-tenant authorization concerns in a strictly single-operator deployment, unless mutually untrusted users are actually present.

## Reviewing a change

1. Identify the attacker, the asset, and the deployment assumptions. Distinguish anonymous calls, stolen credentials, untrusted files, prompt injection, provider responses, and trusted operator selections.
2. Trace the relevant trust boundary from source to consumer. Check authentication, normalization, validation, storage, response or execution, and any required user interaction.
3. Resolve route authorization from the registered application. For file delivery, also verify the exact allowed resource, resolved path, MIME handling, cache policy, and token binding.
4. On outbound paths, check destination and redirect handling, credential forwarding, deadlines, and actual body limits in the client that performs the request.
5. For archives, uploads, and media, review path containment and resource use separately. Consider member count, expanded size, path depth, parser time, concurrency, and failure cleanup.
6. For Agent capabilities, verify the SDK hook, kernel sandbox, and in-process MCP tool paths as applicable. Allowed tool use can still alter or disclose data and incur costs.
7. Label evidence as `Confirmed`, `Conditional`, `Needs validation`, or `Architectural risk`. Report the root cause with its consequences. Use the private advisory process for verified gaps and compare against existing advisories before reporting.

## Reassessment triggers

Rebuild or materially revise this threat model when ArcReel:

- Adds real users, invitations, roles, teams, public sharing, or tenant-specific data.
- Introduces cookie authentication, OAuth/OIDC, refresh tokens, or browser session cookies.
- Changes centralized route registration or adds public/self-authenticating routers.
- Adds public project sharing, signed media URLs, external embeds, or new downloadable file types.
- Changes agent tools, sandbox settings, network policy, MCP capabilities, protected-write rules, or Windows fallback behavior.
- Adds a provider protocol, custom endpoint type, upload path, webhook, URL-fetching feature, or provider-returned artifact type.
- Changes archive structure, import/export behavior, accepted source formats, or media parsers.
- Moves credentials to a different store, adds encryption, changes database deployment, introduces backup automation, or changes how object-storage credentials, references, or signed URLs are issued and used.
- Changes Docker users, capabilities, security profiles, bind mounts, or server/agent process separation.
- Makes Internet exposure a first-class supported deployment mode.
