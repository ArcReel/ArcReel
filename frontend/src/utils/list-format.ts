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

// 名词列表按界面语言出分隔符（zh「、」/ en「, 」）；narrow-conjunction 不加连接词，
// 适合嵌进句子里的名称枚举。
export function formatNameList(items: string[], lang: string): string {
  return getFormatter(lang).format(items);
}
