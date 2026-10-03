import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, ChevronRight, ExternalLink, Loader2, RefreshCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { StreamMarkdown } from "@/components/copilot/StreamMarkdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { formatDate } from "@/utils/date-format";
import { downloadBlob } from "@/utils/download";
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
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const versionLabelId = useId();

  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );

  const handleDownloadDiagnostics = useCallback(async () => {
    if (!mountedRef.current) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const { blob, filename } = await API.downloadDiagnostics();
      downloadBlob(blob, filename);
    } catch (err) {
      if (!mountedRef.current) return;
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mountedRef.current) {
        setDownloading(false);
      }
    }
  }, []);

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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 后异步拉取版本，回调内回写状态
    void fetchVersion();
  }, [fetchVersion]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 text-primary animate-spin" aria-hidden />
        {t("about_loading")}
      </div>
    );
  }

  const latest = data?.latest ?? null;
  const checkError = error ?? data?.update_check_error;

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-lg font-medium">{t("about")}</h2>

      <section aria-labelledby={versionLabelId} className="flex flex-col rounded-lg border border-border bg-card">
        <div className="flex flex-wrap items-start justify-between gap-4 p-4">
          <div className="flex min-w-0 flex-col gap-2">
            <p id={versionLabelId} className="text-xs font-medium text-muted-foreground">
              {t("about_current_version")}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-2xl font-medium tabular-nums">{data?.current.version ?? "-"}</span>
              {data?.has_update ? (
                <Badge>{t("about_update_available")}</Badge>
              ) : (
                <Badge variant="secondary">{t("about_up_to_date")}</Badge>
              )}
            </div>
            <div className="flex flex-col gap-0.5 text-sm text-muted-foreground">
              {latest && <p>{t("about_latest_version", { version: latest.version })}</p>}
              {latest?.published_at && (
                <p>
                  {t("about_published_at", {
                    date: formatDate(latest.published_at, i18n.language, ABOUT_DATE_OPTS, "-"),
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
          <Button variant="outline" onClick={() => void fetchVersion()} disabled={refreshing}>
            <RefreshCcw className={refreshing ? "animate-spin" : undefined} aria-hidden />
            {refreshing ? t("about_checking_update") : t("about_check_update")}
          </Button>
        </div>

        {checkError && (
          <p role="alert" className="mx-4 mb-4 flex items-start gap-2 rounded-md bg-warn/10 px-3 py-2 text-sm text-warn">
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 break-words">{checkError}</span>
          </p>
        )}

        {/* 发布说明默认收起，只展示最新一条；版本卡本身保持一屏内能看完。 */}
        <div className="border-t border-border px-4 py-3">
          <Collapsible>
            <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2.5" />}>
              <ChevronRight aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
              {t("about_release_notes")}
              {latest && <span className="font-normal text-muted-foreground tabular-nums">{latest.version}</span>}
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2">
              <div className="flex flex-col gap-3 pl-6">
                {latest?.body ? (
                  <div className="max-w-prose min-w-0 text-subtle-foreground">
                    <StreamMarkdown content={latest.body} />
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("about_release_notes_empty")}</p>
                )}
                {latest?.html_url && (
                  <a
                    href={latest.html_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-fit items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
                  >
                    {t("about_open_release")}
                    <ExternalLink className="size-3.5" aria-hidden />
                  </a>
                )}
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
      </section>

      <section className="flex flex-col items-start gap-3 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-medium">{t("diagnostics_section_title")}</h3>
          <p className="text-sm text-muted-foreground">{t("diagnostics_section_desc")}</p>
        </div>
        <Button variant="outline" onClick={() => void handleDownloadDiagnostics()} disabled={downloading}>
          {downloading ? t("diagnostics_downloading") : t("diagnostics_download")}
        </Button>
        {downloadError && (
          <p role="alert" className="text-sm text-destructive">
            {t("diagnostics_download_failed", { error: downloadError })}
          </p>
        )}
      </section>

      {/* Copyright & attribution — NOTICE §7(b) 要求的署名句与仓库链接，逐字保留，不走品牌占位 */}
      <section className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium">{t("about_legal_title")}</h3>
        <div className="flex flex-col gap-1 text-sm text-muted-foreground">
          <p>Copyright © 2026 Pollo3470 and ArcReel contributors</p>
          <p>
            Powered by ArcReel —{" "}
            <a
              href="https://github.com/ArcReel/ArcReel"
              target="_blank"
              rel="noreferrer"
              className="break-all text-primary underline-offset-4 hover:underline"
            >
              https://github.com/ArcReel/ArcReel
            </a>
          </p>
        </div>
      </section>
    </div>
  );
}
