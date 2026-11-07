---
status: accepted
---

# Sharing submissions become official-source PRs through the service; no account, with PRs as sole approval container

Manual official-repository PRs were the only contribution path (`docs/adr/0078`), requiring GitHub skills and entry-directory preparation. Add client sharing submissions: send custom endpoints to the official service, which preflights and opens source-repository PRs as a GitHub App. Approval remains on GitHub.

- **No account**: identify submitters by instance ID (`docs/adr/0093`) plus service-issued submission token, used for status/PR links. Optional GitHub usernames are unverified mentions only.
- **PRs alone own approval**: no service approval console or stored entry content, only PR number/terminal state. Merge accepts, close rejects, with webhook `open` → `merged` / `closed`. Reuse manual CI/review.
- **Same-instance resubmission updates the branch**: same slug while open pushes another commit without changing tokens; after closure, create a new PR.
- **Other instances may submit the same slug**: create PRs with `conflict` labels for reviewers.
- **Typed payloads from day one**: `{type, slug, files, meta}`, with `files` mapping relative paths to content in type directories. Initially accept endpoints only; return unsupported-type diagnostics otherwise. Dispatch preflight to source-CI validators; failures return diagnostic codes without PRs.
- Centralize abuse limits for open submissions per instance, daily submissions per IP, and package size in the service.

## Considered Options

- **Store pending content and approve before PR creation**: duplicates GitHub approval state and needs admin login.
- **Require GitHub login**: recreates accounts and excludes the intended less-GitHub-familiar users.
- **Reject cross-instance slug conflicts**: permits squatting; reviewers decide more reliably.
- **Endpoint-only payloads**: future entry types require API/client changes while generic packages cost little.

## Consequences

- Submission creates no installation record or local/market binding (`docs/adr/0079` unchanged).
- Manual PRs coexist with submissions, governed by identical source CI.
- Initially clients query with tokens for results, without email/in-app notifications.
