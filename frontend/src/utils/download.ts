/**
 * 把自由文本变成能安全传给 `<a download>` 的文件名：路径分隔符会被浏览器当成
 * 目录层级处理（含 `/` 的文件名会被存进同名子目录，而不是下载目录根下），
 * 因此替换掉路径分隔符、Windows 保留字符与控制字符；结果为空时回退到 fallback。
 */
export function sanitizeFilename(raw: string, fallback: string): string {
  const cleaned = raw
    .replace(/[/\\]/g, "-")
    // eslint-disable-next-line no-control-regex -- 控制字符本身就是要过滤的目标
    .replace(/[\x00-\x1f:*?"<>|]/g, "")
    .trim()
    .replace(/[. ]+$/, "");
  return cleaned || fallback;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
