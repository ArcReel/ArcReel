/**
 * Normalize/validate Agent credential base_url, matching backend lib/config/url_utils.py exactly.
 *
 * Stored values are called values: Claude CLI SDK always appends /v1/messages,
 * so frontend previews must equal backend probes/runtime request strings.
 */

/** Path segments always appended by Claude CLI. */
export const MESSAGES_PATH = "/v1/messages";

/**
 * Trim edge whitespace/trailing slashes; remove exact trailing /v1/messages.
 *
 * Never strip /vN or standalone /messages, nor recognize/append /anthropic subpaths.
 */
export function normalizeAnthropicBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "");
  // Remove trailing slashes again after endpoint stripping: https://x//v1/messages must store https://x.
  return trimmed.endsWith(MESSAGES_PATH)
    ? trimmed.slice(0, -MESSAGES_PATH.length).replace(/\/+$/, "")
    : trimmed;
}

/** Normalized request previews, equal to backend messages_url. */
export function anthropicMessagesUrl(raw: string): string {
  return normalizeAnthropicBaseUrl(raw) + MESSAGES_PATH;
}

/**
 * Whether inputs contain backend-always-422 query/fragment/userinfo.
 *
 * Other invalid shapes (missing scheme/host, etc.) defer to servers, avoiding duplicated frontend parsing rules.
 */
export function hasUnsupportedUrlComponents(raw: string): boolean {
  const value = raw.trim();
  if (!value) return false;
  if (value.includes("?") || value.includes("#")) return true;
  try {
    const parsed = new URL(value);
    return parsed.username !== "" || parsed.password !== "";
  } catch {
    return false;
  }
}
