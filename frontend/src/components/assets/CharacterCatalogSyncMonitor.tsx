import { useEffect, useRef } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { useAssetsStore } from "@/stores/assets-store";
import { useAuthStore } from "@/stores/auth-store";
import {
  isCharacterCatalogJobActive,
  useCharacterCatalogSyncStore,
} from "@/stores/character-catalog-sync-store";
import { UI_LAYERS } from "@/utils/ui-layers";

const POLL_INTERVAL_MS = 1000;

export function CharacterCatalogSyncMonitor() {
  const { t } = useTranslation("assets");
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const job = useCharacterCatalogSyncStore((state) => state.job);
  const refresh = useCharacterCatalogSyncStore((state) => state.refresh);
  const observedActiveJobId = useRef<string | null>(null);
  const handledTerminalJobId = useRef<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated) return;
    void refresh().catch(() => undefined);
  }, [isAuthenticated, refresh]);

  useEffect(() => {
    if (!isAuthenticated || !isCharacterCatalogJobActive(job)) return;
    const timer = window.setInterval(() => void refresh().catch(() => undefined), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [isAuthenticated, job, refresh]);

  useEffect(() => {
    if (!job) return;
    if (isCharacterCatalogJobActive(job)) {
      observedActiveJobId.current = job.job_id;
      return;
    }
    if (
      observedActiveJobId.current !== job.job_id ||
      handledTerminalJobId.current === job.job_id
    ) return;
    handledTerminalJobId.current = job.job_id;
    observedActiveJobId.current = null;

    if (job.status === "succeeded" && job.result) {
      useAssetsStore.getState().invalidateCharacterCatalog();
      useAppStore.getState().pushNotification(t("sync_library_success", {
        added: job.result.added,
        updated: job.result.updated,
        unchanged: job.result.unchanged,
        assetsDownloaded: job.result.assetsDownloaded,
      }), "success");
    } else if (job.status === "failed") {
      useAppStore.getState().pushNotification(
        job.error_message || t("sync_library_failed"),
        "error",
      );
    }
  }, [job, t]);

  if (!isAuthenticated || !isCharacterCatalogJobActive(job)) return null;

  const hasTotal = job.progress_total > 0;
  const percent = hasTotal
    ? Math.min(100, Math.round((job.progress_current / job.progress_total) * 100))
    : 0;
  const statusText = job.phase === "fetching_catalog"
    ? t("sync_phase_fetching")
    : hasTotal
      ? t("sync_phase_characters", { current: job.progress_current, total: job.progress_total })
      : t("syncing_library");

  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed bottom-5 right-5 ${UI_LAYERS.toast} w-72 rounded-xl border p-3.5 shadow-2xl backdrop-blur-xl`}
      style={{
        borderColor: "var(--color-accent-soft)",
        background: "oklch(0.17 0.012 260 / 0.94)",
        color: "var(--color-text)",
      }}
    >
      <div className="flex items-center gap-2.5">
        {job.status === "queued"
          ? <RefreshCw aria-hidden className="h-4 w-4 text-accent" />
          : <Loader2 aria-hidden className="h-4 w-4 animate-spin text-accent" />}
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-medium">{t("sync_background_title")}</div>
          <div className="mt-0.5 truncate text-[10px] text-text-4">{statusText}</div>
        </div>
        {hasTotal && <span className="num text-[11px] text-text-3">{percent}%</span>}
      </div>
      <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full bg-accent transition-[width] duration-300 ${hasTotal ? "" : "w-1/3 animate-pulse"}`}
          style={hasTotal ? { width: `${percent}%` } : undefined}
        />
      </div>
    </div>
  );
}
