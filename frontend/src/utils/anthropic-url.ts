
export const MESSAGES_PATH = "/v1/messages";

export function normalizeAnthropicBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "");
  return trimmed.endsWith(MESSAGES_PATH)
    ? trimmed.slice(0, -MESSAGES_PATH.length).replace(/\/+$/, "")
    : trimmed;
}

export function anthropicMessagesUrl(raw: string): string {
  return normalizeAnthropicBaseUrl(raw) + MESSAGES_PATH;
}

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
