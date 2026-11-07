---
status: accepted
---

# Instance IDs and installation reporting: resettable random identity, official sources only, enabled with first-use notice

The official service counts installing ArcReel instances and restricts ratings to them, requiring deduplication without accounts (`docs/adr/0078`). Use **instance ID as the sole subject**:

- Generate a random UUID locally on first need, store in system settings, with no machine/user/account association. Users reset it in settings; the service then sees a new instance.
- Every official-service request carries it in a header. Store only salted hashes, not original IDs. Use IP only for rate limiting, without persistence.
- Report once after installation from the **official source** (canonical source key, type, slug, version, app version). Never report updates/uninstalls or third-party installs. Repeated same-entry reports are idempotent; counts never decrease.
- Default reporting on. First market entry explains data and disabling location; after confirmation do not repeat.
- Bundle service URL with env overrides; save the visible master switch in settings. Empty URL or disabled switch hides all official-service elements and produces zero local-server outbound requests.
- Report after install transaction commits. Failures neither affect installation nor retry. Frontend talks only to local server, which proxies all service requests.

## Considered Options

- **Accounts or device fingerprints**: accounts require ongoing operations; fingerprints cannot be reset and exceed anonymous-count needs.
- **Opt-in**: almost no samples and weak installer-only ratings. Default-on notice plus one-click disable yields useful data.
- **Report third-party installs**: leaks privately added source addresses and gives counts irrelevant to official entries.

## Consequences

- Installation records (`docs/adr/0079`) remain unchanged; reporting is post-commit side work and outages do not block installs.
- Ratings require a reported installation; resetting identity abandons the previous rating identity.
- White-label/self-hosted distributions disable the whole service with an empty URL.
