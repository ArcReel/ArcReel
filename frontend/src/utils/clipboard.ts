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
  // execCommand reports failure through its return value without throwing; without conversion to rejection, callers would treat failure as success
  if (!copied) throw new Error("clipboard copy failed");
}
