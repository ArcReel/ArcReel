import { useEffect, useState } from "react";
import { ExternalLink, Info, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";
import type { GetSystemVersionResponse } from "@/types";

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
    // 仅 mount 时拉一次；t 仅用于 fallback 错误文案，不应触发重新拉取 GitHub Release
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
    return <div className="rounded-2xl border border-gray-800 bg-gray-900/40 p-6 text-sm text-gray-400">{t("about_loading")}</div>;
  }

  return (
    <section className="space-y-6">
      <div className="rounded-2xl border border-gray-800 bg-gray-900/60 p-6 shadow-[0_20px_80px_rgba(15,23,42,0.35)]">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div className="space-y-3">
            <p className="text-xs uppercase tracking-[0.24em] text-gray-500">{t("about_current_version")}</p>
            <div className="flex items-end gap-3">
              <span className="text-3xl font-semibold text-white">{data?.current.version ?? "-"}</span>
              {data?.has_update ? (
                <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300">
                  {t("about_update_available")}
                </span>
              ) : (
                <span className="rounded-full border border-gray-700 bg-gray-800/70 px-2.5 py-1 text-xs text-gray-300">
                  {t("about_up_to_date")}
                </span>
              )}
            </div>
            <div className="space-y-1 text-sm text-gray-300">
              {data?.latest && <p>{t("about_latest_version", { version: data.latest.version })}</p>}
              {data?.latest?.published_at && (
                <p>{t("about_published_at", { date: formatDate(data.latest.published_at, i18n.language) })}</p>
              )}
              <p>{t("about_checked_at", { date: formatDate(data?.checked_at ?? "", i18n.language) })}</p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void handleRefresh()}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-700 bg-gray-950/70 px-4 py-2.5 text-sm font-medium text-gray-100 transition hover:border-gray-600 hover:bg-gray-800 focus-ring"
          >
            <RefreshCcw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? t("about_checking_update") : t("about_check_update")}
          </button>
        </div>

        {(error || data?.update_check_error) && (
          <div className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
            {error ?? data?.update_check_error}
          </div>
        )}

        {data?.latest?.html_url && (
          <a
            href={data.latest.html_url}
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-flex items-center gap-2 text-sm text-sky-300 transition hover:text-sky-200"
          >
            {t("about_open_release")}
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      <div className="rounded-2xl border border-gray-800 bg-gray-900/40 p-6">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-100">
          <Info className="h-4 w-4 text-sky-300" />
          <span>{t("about_release_notes")}</span>
        </div>
        {data?.latest?.body ? (
          <div className="markdown-body text-sm leading-6 text-gray-200">
            <StreamMarkdown content={data.latest.body} />
          </div>
        ) : (
          <p className="text-sm text-gray-500">{t("about_release_notes_empty")}</p>
        )}
      </div>
    </section>
  );
}
