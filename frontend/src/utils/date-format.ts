const cache = new Map<string, Intl.DateTimeFormat>();

function getFormatter(lang: string | undefined, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${lang ?? ""}|${JSON.stringify(options)}`;
  let fmt = cache.get(key);
  if (!fmt) {
    try {
      fmt = new Intl.DateTimeFormat(lang, options);
    } catch {
      fmt = new Intl.DateTimeFormat(undefined, options);
    }
    cache.set(key, fmt);
  }
  return fmt;
}

export function formatDate(
  value: string | Date | null | undefined,
  lang: string,
  options: Intl.DateTimeFormatOptions,
  fallback = "—",
): string {
  if (value === null || value === undefined || value === "") return fallback;
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return fallback;
  return getFormatter(lang, options).format(date);
}
