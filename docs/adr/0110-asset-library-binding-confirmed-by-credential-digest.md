---
status: accepted
---

# Confirm library bindings against credential content; sites determine control-plane hosts

Provider-library control planes accept Access Key signatures, separate from inference API Keys; only users know account/project ownership. Store library accounts separately and require users to explicitly bind inference credentials to site/region/project resource scope. Confirmation records a digest of credential content, endpoint, and account scope, recomputed on reads. Any change returns pending confirmation and later calls act unbound. Reject permanent record-ID binding: credentials may change accounts or endpoints in place, causing old-account asset IDs to be sent to another account.

Control-plane requests go only to registered official site/region hosts (`ark.cn-beijing.volcengineapi.com` for Volcengine, `ark.ap-southeast-1.byteplusapi.com` for BytePlus), never inferred from inference base_url. Users can redirect base_url to arbitrary relays; inferring hosts hands them control-plane secrets. Custom providers can bind too; do not guess asset-reference support by domain.

## Consequences

Key rotation or endpoint changes require reconfirmation in settings, one extra action rather than silent reuse of old-account eligibility. Binding expresses user confirmation only; providers authorize every call themselves. Add hosts to site registrations when official documentation adds regions. Scope/acceptance: [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559).
