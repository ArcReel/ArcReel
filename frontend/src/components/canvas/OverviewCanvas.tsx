
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import type { ProjectData } from "@/types";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { formatCost, totalBreakdown } from "@/utils/cost-format";

import { WelcomeCanvas } from "./WelcomeCanvas";

interface OverviewCanvasProps {
  projectName: string;
  projectData: ProjectData | null;
}

export function OverviewCanvas({ projectName, projectData }: OverviewCanvasProps) {
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  tRef.current = t;
  const projectTotals = useCostStore((s) => s.costData?.project_totals);
  const getEpisodeCost = useCostStore((s) => s.getEpisodeCost);
  const costLoading = useCostStore((s) => s.loading);
  const costError = useCostStore((s) => s.error);
  const debouncedFetch = useCostStore((s) => s.debouncedFetch);

  useEffect(() => {
    if (!projectName) return;
    debouncedFetch(projectName);
  }, [projectName, projectData?.episodes, debouncedFetch]);

  const [regenerating, setRegenerating] = useState(false);

  const refreshProject = useCallback(
    async () => {
      const res = await API.getProject(projectName);
      useProjectsStore.getState().setCurrentProject(
        projectName,
        res.project,
        res.scripts ?? {},
        res.asset_fingerprints,
      );
    },
    [projectName],
  );

  const handleUpload = useCallback(
    async (file: File) => {
      await API.uploadFile(projectName, "source", file);
      useAppStore.getState().pushToast(tRef.current("source_file_upload_success", { name: file.name }), "success");
    },
    [projectName],
  );

  const handleAnalyze = useCallback(async () => {
    await API.generateOverview(projectName);
    await refreshProject();
  }, [projectName, refreshProject]);

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    try {
      await API.generateOverview(projectName);
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("project_overview_regenerated"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${tRef.current("regenerate_failed")}${(err as Error).message}`, "error");
    } finally {
      setRegenerating(false);
    }
  }, [projectName, refreshProject]);

  if (!projectData) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        {t("loading_project_data")}
      </div>
    );
  }

  const status = projectData.status;
  const overview = projectData.overview;
  const showWelcome = !overview && (projectData.episodes?.length ?? 0) === 0;
  const focusRing = "focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 focus-visible:ring-offset-gray-900";

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-6 p-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">{projectData.title}</h1>
          <p className="mt-1 text-sm text-gray-400">
            {projectData.content_mode === "narration"
              ? t("narration_visuals_mode")
              : t("drama_animation_mode")}
          </p>
        </div>

        {showWelcome ? (
          <WelcomeCanvas
            projectName={projectName}
            projectTitle={projectData.title}
            onUpload={handleUpload}
            onAnalyze={handleAnalyze}
          />
        ) : (
          <>
            {overview && (
              <div className="space-y-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-gray-300">{t("project_overview_title")}</h3>
                  <button
                    type="button"
                    onClick={() => void handleRegenerate()}
                    disabled={regenerating}
                    className={`flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
                    title={t("regen_overview_title")}
                  >
                    <RefreshCw
                      className={`h-3 w-3 ${regenerating ? "animate-spin" : ""}`}
                    />
                    <span>{regenerating ? t("regenerating_short") : t("regen_short")}</span>
                  </button>
                </div>
                <p className="text-sm text-gray-400">{overview.synopsis}</p>
                <div className="flex gap-4 text-xs text-gray-500">
                  <span>{t("genre_prefix")}{overview.genre}</span>
                  <span>{t("theme_prefix")}{overview.theme}</span>
                </div>
              </div>
            )}

            {status && (
              <div className="grid grid-cols-2 gap-3">
                {(["characters", "scenes", "props"] as const).map(
                  (key) => {
                    const cat = status[key] as
                      | { total: number; completed: number }
                      | undefined;
                    if (!cat) return null;
                    const pct =
                      cat.total > 0
                        ? Math.round((cat.completed / cat.total) * 100)
                        : 0;
                    const labels: Record<string, string> = {
                      characters: t("characters"),
                      scenes: t("scenes"),
                      props: t("props"),
                    };
                    return (
                      <div
                        key={key}
                        className="rounded-lg border border-gray-800 bg-gray-900 p-3"
                      >
                        <div className="mb-1 flex justify-between text-xs">
                          <span className="text-gray-400">{labels[key]}</span>
                          <span className="text-gray-300">
                            {cat.completed}/{cat.total}
                          </span>
                        </div>
                        <div
                          className="h-1.5 overflow-hidden rounded-full bg-gray-800"
                          role="progressbar"
                          aria-valuenow={pct}
                          aria-valuemin={0}
                          aria-valuemax={100}
                        >
                          <div
                            className="h-full rounded-full bg-indigo-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  },
                )}
              </div>
            )}

            {costLoading && (
              <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
                <p className="text-sm text-gray-500 animate-pulse">{t("calculating_cost")}</p>
              </div>
            )}
            {costError && (
              <div className="rounded-xl border border-red-900/50 bg-red-950/30 p-4">
                <p className="text-sm text-red-400">{t("cost_estimate_failed")}{costError}</p>
              </div>
            )}

            {projectTotals && (
              <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 tabular-nums">
                <p className="mb-3 text-sm font-semibold text-gray-300">{t("project_total_cost")}</p>
                <dl className="flex flex-wrap items-start justify-between gap-6">
                  <div className="min-w-0">
                    <dt className="mb-1 text-[11px] text-gray-600">{t("estimate")}</dt>
                    <dd className="text-sm text-gray-400">
                      <span className="text-gray-500">{t("storyboard")} </span>
                      <span className="text-gray-200">{formatCost(projectTotals.estimate.image)}</span>
                      <span className="ml-3 text-gray-500">{t("video")} </span>
                      <span className="text-gray-200">{formatCost(projectTotals.estimate.video)}</span>
                      <span className="ml-3 text-gray-500">{t("total")} </span>
                      <span className="font-semibold text-amber-400">{formatCost(totalBreakdown(projectTotals.estimate))}</span>
                    </dd>
                  </div>
                  <div role="separator" className="h-8 w-px bg-gray-800" />
                  <div className="min-w-0">
                    <dt className="mb-1 text-[11px] text-gray-600">{t("actual")}</dt>
                    <dd className="text-sm text-gray-400">
                      <span className="text-gray-500">{t("storyboard")} </span>
                      <span className="text-gray-200">{formatCost(projectTotals.actual.image)}</span>
                      <span className="ml-3 text-gray-500">{t("video")} </span>
                      <span className="text-gray-200">{formatCost(projectTotals.actual.video)}</span>
                      {(["characters", "scenes", "props"] as const).map((kind) => {
                        const bucket = projectTotals.actual[kind];
                        if (!bucket) return null;
                        return (
                          <span key={kind} className="ml-3">
                            <span className="text-gray-500">{t(`actual_${kind}`)} </span>
                            <span className="text-gray-200">{formatCost(bucket)}</span>
                          </span>
                        );
                      })}
                      <span className="ml-3 text-gray-500">{t("total")} </span>
                      <span className="font-semibold text-emerald-400">{formatCost(totalBreakdown(projectTotals.actual))}</span>
                    </dd>
                  </div>
                </dl>
              </div>
            )}

            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-gray-300">{t("episodes_title")}</h3>
              {(projectData.episodes?.length ?? 0) === 0 ? (
                <p className="text-sm text-gray-500">
                  {t("no_episodes_ai_hint")}
                </p>
              ) : (
                (projectData.episodes ?? []).map((ep) => {
                  const epCost = getEpisodeCost(ep.episode);
                  return (
                    <div
                      key={ep.episode}
                      className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 px-4 py-2.5 tabular-nums"
                    >
                      <span className="font-mono text-xs text-gray-400">
                        E{ep.episode}
                      </span>
                      <span className="text-sm text-gray-200">{ep.title}</span>
                      <span className="text-xs text-gray-500">
                        {t("segments_and_status", { count: ep.scenes_count ?? "?", status: ep.status ?? "draft" })}
                      </span>
                      {epCost && (
                        <span className="ml-auto flex min-w-0 flex-shrink flex-wrap gap-4 text-xs text-gray-400">
                          <span>
                            <span className="text-gray-500">{t("estimate")} </span>
                            <span className="text-gray-500">{t("storyboard")} </span><span className="text-gray-300">{formatCost(epCost.totals.estimate.image)}</span>
                            <span className="ml-2 text-gray-500">{t("video")} </span><span className="text-gray-300">{formatCost(epCost.totals.estimate.video)}</span>
                            <span className="ml-2 text-gray-500">{t("total")} </span><span className="font-medium text-amber-400">{formatCost(totalBreakdown(epCost.totals.estimate))}</span>
                          </span>
                          <span className="text-gray-700">|</span>
                          <span>
                            <span className="text-gray-500">{t("actual")} </span>
                            <span className="text-gray-500">{t("storyboard")} </span><span className="text-gray-300">{formatCost(epCost.totals.actual.image)}</span>
                            <span className="ml-2 text-gray-500">{t("video")} </span><span className="text-gray-300">{formatCost(epCost.totals.actual.video)}</span>
                            <span className="ml-2 text-gray-500">{t("total")} </span><span className="font-medium text-emerald-400">{formatCost(totalBreakdown(epCost.totals.actual))}</span>
                          </span>
                        </span>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </>
        )}

        <div className="h-8" />
      </div>
    </div>
  );
}
