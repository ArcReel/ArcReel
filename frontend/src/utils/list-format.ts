const cache = new Map<string, Intl.ListFormat>();

function getFormatter(lang: string | undefined): Intl.ListFormat {
  const key = lang ?? "";
  let fmt = cache.get(key);
  if (!fmt) {
    try {
      fmt = new Intl.ListFormat(lang, { style: "narrow", type: "conjunction" });
    } catch {
      fmt = new Intl.ListFormat(undefined, { style: "narrow", type: "conjunction" });
    }
    cache.set(key, fmt);
  }
  return fmt;
}

// Use separators matching the UI language for noun lists (zh “、” / en “, ”); narrow-conjunction adds no conjunction,
// suitable for lists of names embedded in a sentence.
export function formatNameList(items: string[], lang: string): string {
  return getFormatter(lang).format(items);
}
