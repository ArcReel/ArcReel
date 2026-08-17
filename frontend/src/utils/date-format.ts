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

// ISO 字符串若没有显式时区后缀（Z / ±HH(:MM)），按 UTC 处理避免浏览器歧义
function parseIso(value: string): Date {
  const hasTz = /(?:Z|[+-]\d{2}(?::?\d{2})?)$/.test(value);
  return new Date(hasTz ? value : `${value}Z`);
}

export function formatDate(
  value: string | Date | null | undefined,
  lang: string,
  options: Intl.DateTimeFormatOptions,
  fallback = "—",
): string {
  if (value === null || value === undefined || value === "") return fallback;
  const date = typeof value === "string" ? parseIso(value) : value;
  if (Number.isNaN(date.getTime())) return fallback;
  return getFormatter(lang, options).format(date);
}

// 本地时刻 HH:mm，用于同一天内的对话流；解析失败返回 null 由调用方兜底
export function formatClockTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = parseIso(value);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 紧凑本地时间 MM/DD HH:mm，用于信息密集的列表/标题；解析失败返回 null 由调用方兜底
export function formatShortDateTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = parseIso(value);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
