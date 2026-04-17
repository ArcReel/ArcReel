const SAFE_IMAGE_PROTOCOLS = new Set(["http:", "https:", "blob:", "data:"]);

export function sanitizeImageSrc(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  let url: URL;
  try {
    url = new URL(trimmed, window.location.origin);
  } catch {
    return undefined;
  }
  if (!SAFE_IMAGE_PROTOCOLS.has(url.protocol)) return undefined;
  if (url.protocol === "data:" && !/^data:image\//i.test(url.href)) return undefined;
  // 通过 URL 对象重建输出，让静态分析器识别为已归一化的安全 URL
  return url.toString();
}
