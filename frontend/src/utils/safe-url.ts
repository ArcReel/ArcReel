/**
 * Validate post-login return destinations to prevent open redirects.
 * Allow only same-origin internal addresses whose paths fall under /app/; reject external sites, protocol-relative URLs (//host),
 * and non-app pages such as /login, returning null for callers to fall back to the default page.
 */
export function safeReturnPath(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw, window.location.origin);
  } catch {
    return null;
  }
  if (url.origin !== window.location.origin) return null;
  if (!url.pathname.startsWith("/app/")) return null;
  return url.pathname + url.search + url.hash;
}
