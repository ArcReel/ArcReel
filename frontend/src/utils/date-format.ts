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

// ISO 字符串若没有显式时区后缀（Z / ±HH(:MM)），按 UTC 处理避免浏览器歧义。
// 时间戳解析规则在时刻展示与时长计算之间共用，故导出。
export function parseIsoTimestamp(value: string): Date {
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
  const date = typeof value === "string" ? parseIsoTimestamp(value) : value;
  if (Number.isNaN(date.getTime())) return fallback;
  return getFormatter(lang, options).format(date);
}

// 本地时刻 HH:mm，用于同一天内的对话流；解析失败返回 null 由调用方兜底
export function formatClockTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = parseIsoTimestamp(value);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 紧凑本地时间 MM/DD HH:mm，用于信息密集的列表/标题；解析失败返回 null 由调用方兜底
export function formatShortDateTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = parseIsoTimestamp(value);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

// 相对当前的粗粒度时间（「3 小时前」），按界面语言成文；不足一分钟算「现在」，解析失败返回 null
export function formatRelativeTime(
  value: string | null | undefined,
  lang: string,
  now: number = Date.now(),
): string | null {
  if (!value) return null;
  const time = parseIsoTimestamp(value).getTime();
  if (Number.isNaN(time)) return null;
  const diff = time - now;
  let fmt: Intl.RelativeTimeFormat;
  try {
    fmt = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  } catch {
    fmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  }
  for (const [unit, ms] of RELATIVE_UNITS) {
    if (Math.abs(diff) >= ms) return fmt.format(Math.round(diff / ms), unit);
  }
  return fmt.format(0, "second");
}
