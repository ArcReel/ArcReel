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

export function formatClockTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = parseIsoTimestamp(value);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

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

const JUST_NOW_MS = 60_000;

const relativeCache = new Map<string, Intl.RelativeTimeFormat>();

function getRelativeFormatter(lang: string): Intl.RelativeTimeFormat {
  let fmt = relativeCache.get(lang);
  if (!fmt) {
    try {
      fmt = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    } catch {
      fmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    }
    relativeCache.set(lang, fmt);
  }
  return fmt;
}

export function isJustNow(value: string | null | undefined, now: number = Date.now()): boolean {
  if (!value) return false;
  const time = parseIsoTimestamp(value).getTime();
  return !Number.isNaN(time) && Math.abs(time - now) < JUST_NOW_MS;
}

export function formatRelativeTime(
  value: string | null | undefined,
  lang: string,
  now: number = Date.now(),
): string | null {
  if (!value) return null;
  const time = parseIsoTimestamp(value).getTime();
  if (Number.isNaN(time)) return null;
  const diff = time - now;
  const fmt = getRelativeFormatter(lang);
  for (const [unit, ms] of RELATIVE_UNITS) {
    if (Math.abs(diff) >= ms) return fmt.format(Math.round(diff / ms), unit);
  }
  return fmt.format(0, "second");
}
