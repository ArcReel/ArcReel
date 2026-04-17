const SAFE_IMAGE_PROTOCOLS = new Set(["http:", "https:", "blob:", "data:"]);

export function sanitizeImageSrc(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  if (trimmed.startsWith("/") || trimmed.startsWith("./") || trimmed.startsWith("../")) {
    return trimmed;
  }
  try {
    const url = new URL(trimmed, window.location.origin);
    if (!SAFE_IMAGE_PROTOCOLS.has(url.protocol)) return undefined;
    if (url.protocol === "data:" && !/^data:image\//i.test(trimmed)) return undefined;
    return trimmed;
  } catch {
    return undefined;
  }
}
