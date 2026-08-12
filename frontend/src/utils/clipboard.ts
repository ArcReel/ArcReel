/** Copy text to clipboard. Falls back to execCommand for non-secure contexts (plain HTTP). */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text);
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(ta);
  // execCommand 以返回值报告失败，不抛异常；不转成 reject 的话调用方会把失败当成功
  if (!copied) throw new Error("clipboard copy failed");
}
