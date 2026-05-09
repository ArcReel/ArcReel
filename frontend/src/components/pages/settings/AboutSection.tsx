import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, Info, Loader2, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";
import { CARD_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { formatDate } from "@/utils/date-format";
import type { GetSystemVersionResponse } from "@/types";

const ABOUT_DATE_OPTS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

export function AboutSection() {
  const { t, i18n } = useTranslation("dashboard");
  const [data, setData] = useState<GetSystemVersionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  const fetchVersion = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const result = await API.getSystemVersion();
      if (mountedRef.current) setData(result);
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : t("about_load_failed"));
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [t]);

  useEffect(() => {
    void fetchVersion();
    // 仅 mount 时拉一次；fetchVersion 闭包稳定（仅依赖 t）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
                    date: formatDate(data.latest.published_at, i18n.language, ABOUT_DATE_OPTS, "-"),
                  })}
                </p>
              )}
              <p>
                {t("about_checked_at", {
                  date: formatDate(data?.checked_at ?? "", i18n.language, ABOUT_DATE_OPTS, "-"),
                })}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void fetchVersion()}
            className={`${GHOST_BTN_LG_CLS} justify-center`}
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
            role="alert"
            className="mt-5 flex items-start gap-1.5 rounded-[8px] border px-4 py-3 text-[12px]"
            style={{
              borderColor: "var(--color-warm-ring)",
              background: "var(--color-warm-tint)",
              color: "var(--color-warm-bright)",
            }}
          >
            <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error ?? data?.update_check_error}</span>
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
