import { useEffect, useState, type CSSProperties } from "react";
import { ExternalLink, Info, Loader2, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";
import type { GetSystemVersionResponse } from "@/types";

const CARD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.55), oklch(0.16 0.010 265 / 0.55))",
};

function formatDate(value: string, locale: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function AboutSection() {
  const { t, i18n } = useTranslation("dashboard");
  const [data, setData] = useState<GetSystemVersionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setRefreshing(true);
    void (async () => {
      try {
        const result = await API.getSystemVersion();
        if (mounted) setData(result);
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : t("about_load_failed"));
      } finally {
        if (mounted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
    // 仅 mount 时拉一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleRefresh() {
    setError(null);
    setRefreshing(true);
    try {
      const result = await API.getSystemVersion();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("about_load_failed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  if (loading) {
    return (
      <div
        className="rounded-[10px] border border-hairline px-5 py-6 text-[12.5px] text-text-3"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
          <span className="font-mono text-[10.5px] uppercase tracking-[0.14em]">
            {t("about_loading")}
          </span>
        </div>
      </div>
    );
  }

  return (
    <section className="space-y-6">
      {/* Hero version card */}
      <div
        className="relative overflow-hidden rounded-[12px] border border-hairline p-6"
        style={CARD_STYLE}
      >
        {/* Decorative sprocket-style dots, top-right */}
        <div
          aria-hidden
          className="pointer-events-none absolute right-5 top-5 hidden gap-[4px] sm:flex"
          style={{ opacity: 0.4 }}
        >
          {Array.from({ length: 5 }).map((_, i) => (
            <span
              key={i}
              className="block h-[5px] w-[5px] rounded-full"
              style={{ background: "var(--color-hairline-strong)" }}
            />
          ))}
        </div>

        <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
          <div className="space-y-3">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
              {t("about_current_version")}
            </div>
            <div className="flex items-end gap-3">
              <span
                className="font-editorial"
                style={{
                  fontSize: 44,
                  fontWeight: 400,
                  letterSpacing: "-0.02em",
                  lineHeight: 1,
                  color: "var(--color-text)",
                }}
              >
                {data?.current.version ?? "-"}
              </span>
              {data?.has_update ? (
                <span
                  className="rounded-full px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
                  style={{
                    background: "var(--color-accent-dim)",
                    color: "var(--color-accent-2)",
                    border: "1px solid var(--color-accent-soft)",
                    boxShadow: "0 0 14px -6px var(--color-accent-glow)",
                  }}
                >
                  {t("about_update_available")}
                </span>
              ) : (
                <span className="rounded-full border border-hairline-soft bg-bg-grad-a/55 px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">
                  {t("about_up_to_date")}
                </span>
              )}
            </div>
            <div className="space-y-0.5 text-[12.5px] text-text-3">
              {data?.latest && (
                <p>{t("about_latest_version", { version: data.latest.version })}</p>
              )}
              {data?.latest?.published_at && (
                <p>
                  {t("about_published_at", {
                    date: formatDate(data.latest.published_at, i18n.language),
                  })}
                </p>
              )}
              <p>
                {t("about_checked_at", {
                  date: formatDate(data?.checked_at ?? "", i18n.language),
                })}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void handleRefresh()}
            className="inline-flex items-center justify-center gap-2 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3.5 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <RefreshCcw
              className={`h-3.5 w-3.5 ${refreshing ? "motion-safe:animate-spin" : ""}`}
              aria-hidden
            />
            {refreshing ? t("about_checking_update") : t("about_check_update")}
          </button>
        </div>

        {(error || data?.update_check_error) && (
          <div
            className="mt-5 rounded-[8px] border px-4 py-3 text-[12px]"
            style={{
              borderColor: "var(--color-warm-ring)",
              background: "var(--color-warm-tint)",
              color: "var(--color-warm-bright)",
            }}
          >
            <span className="mr-1.5" aria-hidden>
              ▲
            </span>
            {error ?? data?.update_check_error}
          </div>
        )}

        {data?.latest?.html_url && (
          <a
            href={data.latest.html_url}
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-flex items-center gap-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2 transition-colors hover:text-accent"
          >
            {t("about_open_release")}
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        )}
      </div>

      {/* Release notes */}
      <div
        className="rounded-[12px] border border-hairline p-6"
        style={CARD_STYLE}
      >
        <div className="mb-3 flex items-center gap-2">
          <Info className="h-3.5 w-3.5 text-accent-2" aria-hidden />
          <span className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
            {t("about_release_notes")}
          </span>
        </div>
        {data?.latest?.body ? (
          <div className="markdown-body text-[13px] leading-[1.65] text-text-2">
            <StreamMarkdown content={data.latest.body} />
          </div>
        ) : (
          <p className="text-[12.5px] text-text-3">{t("about_release_notes_empty")}</p>
        )}
      </div>
    </section>
  );
}
