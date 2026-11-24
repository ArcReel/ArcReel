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

export function formatNameList(items: string[], lang: string): string {
  return getFormatter(lang).format(items);
}
