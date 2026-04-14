
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, RefreshCw, Trash2, Upload } from "lucide-react";
import type { ProjectData } from "@/types";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { ProgressBar } from "@/components/ui/ProgressBar";
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
  const styleImageFp = useProjectsStore(
    (s) => projectData?.style_image ? s.getAssetFingerprint(projectData.style_image) : null,
  );
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
  const [uploadingStyleImage, setUploadingStyleImage] = useState(false);
  const [deletingStyleImage, setDeletingStyleImage] = useState(false);
  const [savingStyleDescription, setSavingStyleDescription] = useState(false);
  const [styleDescriptionDraft, setStyleDescriptionDraft] = useState(
    projectData?.style_description ?? "",
  );
  const styleInputRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    setStyleDescriptionDraft(projectData?.style_description ?? "");
  }, [projectData?.style_description]);

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

  const handleStyleImageChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;

      setUploadingStyleImage(true);
      try {
        await API.uploadStyleImage(projectName, file);
        await refreshProject();
        useAppStore.getState().pushToast(tRef.current("style_image_updated"), "success");
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(`${tRef.current("upload_failed")}${(err as Error).message}`, "error");
      } finally {
        setUploadingStyleImage(false);
      }
    },
    [projectName, refreshProject],
  );

  const handleDeleteStyleImage = useCallback(async () => {
    if (deletingStyleImage || !projectData?.style_image) return;
    if (!confirm(tRef.current("confirm_delete_style_image"))) return;

    setDeletingStyleImage(true);
    try {
      await API.deleteStyleImage(projectName);
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("style_image_deleted"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${tRef.current("delete_failed")}${(err as Error).message}`, "error");
    } finally {
      setDeletingStyleImage(false);
    }
  }, [deletingStyleImage, projectData?.style_image, projectName, refreshProject]);

  const handleSaveStyleDescription = useCallback(async () => {
    if (savingStyleDescription) return;
    setSavingStyleDescription(true);
    try {
      await API.updateStyleDescription(projectName, styleDescriptionDraft.trim());
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("style_desc_saved"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${tRef.current("save_failed")}${(err as Error).message}`, "error");
    } finally {
      setSavingStyleDescription(false);
    }
  }, [projectName, refreshProject, savingStyleDescription, styleDescriptionDraft]);

  if (!projectData) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        {t("loading_project_data")}
      </div>
    );
  }

  const status = projectData.status;
  const overview = projectData.overview;
  const styleImageUrl = projectData.style_image
    ? API.getFileUrl(projectName, projectData.style_image, styleImageFp)
    : null;
  const styleDescriptionDirty =
    styleDescriptionDraft !== (projectData.style_description ?? "");
  const showWelcome = !overview && (projectData.episodes?.length ?? 0) === 0;
  const focusRing = "focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-1 focus-visible:ring-offset-gray-900";
  const projectStyleCard = (
    <section className="rounded-2xl border border-gray-800 bg-gray-900/90 p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-gray-200">{t("project_style_title")}</h3>
          <p className="max-w-2xl text-xs leading-5 text-gray-500">
            {t("style_desc_hint")}
          </p>
        </div>
        <div className="inline-flex items-center rounded-full border border-gray-700 bg-gray-800 px-3 py-1 text-xs text-gray-300">
          {projectData.style || t("style_tag_unset")}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
        <div className="space-y-3">
          {styleImageUrl ? (
            <PreviewableImageFrame src={styleImageUrl} alt={t("visual_style_reference")}>
              <div className="overflow-hidden rounded-xl border border-gray-700 bg-gray-950/70">
                <img
                  src={styleImageUrl}
                  alt={t("visual_style_reference")}
                  className="aspect-[4/3] w-full object-cover"
                />
              </div>
            </PreviewableImageFrame>
          ) : (
            <button
              type="button"
              onClick={() => styleInputRef.current?.click()}
              disabled={uploadingStyleImage}
              className={`flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-gray-700 bg-gray-950/40 px-4 text-sm text-gray-500 transition-colors hover:border-gray-500 hover:text-gray-300 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
            >
              <Upload className="h-4 w-4" />
              <span>{uploadingStyleImage ? t("uploading_style_image") : t("upload_style_reference")}</span>
              <span className="text-xs text-gray-600">{t("supported_formats")}</span>
            </button>
          )}

          <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-3">
            <p className="text-xs font-medium text-gray-400">{t("usage_guide")}</p>
            <p className="mt-1 text-sm leading-6 text-gray-300">
              {styleImageUrl
                ? t("style_usage_with_image")
                : t("style_usage_without_image")}
            </p>

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => styleInputRef.current?.click()}
                disabled={uploadingStyleImage}
                className={`inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-300 transition-colors hover:border-gray-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
              >
                <ImagePlus className="h-4 w-4" />
                {styleImageUrl ? t("replace_reference") : t("upload_reference")}
              </button>
              {styleImageUrl && (
                <button
                  type="button"
                  onClick={() => void handleDeleteStyleImage()}
                  disabled={deletingStyleImage}
                  className={`inline-flex items-center gap-1.5 rounded-lg border border-red-500/30 px-3 py-2 text-sm text-red-300 transition-colors hover:border-red-400/50 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
                >
                  <Trash2 className="h-4 w-4" />
                  {deletingStyleImage ? t("deleting_reference") : t("delete_reference")}
                </button>
              )}
            </div>
          </div>

          <input
            ref={styleInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            onChange={handleStyleImageChange}
            className="hidden"
            aria-label={t("upload_style_ref_aria")}
          />
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-950/35 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label htmlFor="style-description-textarea" className="text-xs font-medium text-gray-400">{t("style_description")}</label>
            <span className="text-[11px] text-gray-600">
              {t("style_desc_char_count", { count: styleDescriptionDraft.trim().length })}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            {t("style_desc_auto_hint")}
          </p>

          <textarea
            id="style-description-textarea"
            value={styleDescriptionDraft}
            onChange={(e) => setStyleDescriptionDraft(e.target.value)}
            rows={8}
            className={`mt-3 min-h-44 w-full rounded-xl border border-gray-700 bg-gray-800/80 px-4 py-3 text-sm leading-relaxed text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus-ring`}
            placeholder={t("style_desc_textarea_placeholder")}
          />

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs leading-5 text-gray-500">
              {styleImageUrl
                ? t("style_tip_with_image")
                : t("style_tip_without_image")}
            </p>
            {styleDescriptionDirty && (
              <button
                type="button"
                onClick={() => void handleSaveStyleDescription()}
                disabled={savingStyleDescription}
                className={`rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
              >
                {savingStyleDescription ? t("common:saving") : t("save_style_description")}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-6 p-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">{projectData.title}</h1>
          <p className="mt-1 text-sm text-gray-400">
            {projectData.content_mode === "narration"
              ? t("narration_visuals_mode")
              : t("drama_animation_mode")}{" "}
            · {projectData.style || t("style_not_set")}
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
                {(["characters", "clues"] as const).map(
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
                      clues: t("clues"),
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
                        <ProgressBar value={pct} />
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
                      {projectTotals.actual.character_and_clue && (
                        <>
                          <span className="ml-3 text-gray-500">{t("character_and_clue")} </span>
                          <span className="text-gray-200">{formatCost(projectTotals.actual.character_and_clue)}</span>
                        </>
                      )}
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

        {projectStyleCard}

        <div className="h-8" />
      </div>
    </div>
  );
}
