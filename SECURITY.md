# Security Policy

## Supported versions

ArcReel provides security fixes for the latest release and the current `main` branch. Older releases do not receive guaranteed backports; users should upgrade before requesting a security fix.

## Supported deployment boundary

ArcReel currently supports:

- local deployments used by one trusted operator; and
- private remote deployments used by one trusted operator, with authentication enabled and transport protected by TLS, a VPN, or a secure tunnel.

Direct Internet exposure is not currently supported. Instances shared by mutually untrusted users are also unsupported because ArcReel does not provide tenant isolation, role-based access control, or per-user project authorization.

Setting `AUTH_ENABLED=false` removes login and token checks. Use it only when an independent network boundary confines the service to a trusted local environment.

See the [security threat model](docs/security/threat-model.md) for the complete trust boundaries, assumptions, and reassessment triggers.

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub Private Vulnerability Reporting](https://github.com/ArcReel/ArcReel/security/advisories/new). Include the affected version or commit, prerequisites, impact, and a minimal reproducer when possible.

Do not disclose an unresolved vulnerability through a public issue, discussion, chat group, support channel, or pull request.

## Response and coordinated disclosure

The maintainers do not promise a fixed response or remediation service level. Reports are assessed individually, and the reporter and maintainers coordinate severity, remediation, and disclosure timing.

Keep the report private until a fix is released or a disclosure date is agreed. If a prompt fix is not practical, the maintainers and reporter will coordinate an appropriate disclosure scope and schedule.
