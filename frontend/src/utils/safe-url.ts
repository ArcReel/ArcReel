/**
 * 校验登录后的回跳目标，杜绝 open redirect。
 * 仅放行同源、且路径落在 /app/ 下的站内地址；外站、协议相对（//host）、
 * 以及 /login 等非应用页面一律拒绝，返回 null（由调用方回退到默认页）。
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
