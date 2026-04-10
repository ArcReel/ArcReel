
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, RefreshCw, Trash2, Upload } from "lucide-react";
import type { ProjectData } from "@/types";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
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
  }, [projectName, refreshProject]);

  const handleSaveStyleDescription = useCallback(async () => {
    setSavingStyleDescription(true);
    try {
      await API.updateProject(projectName, {
        style_description: styleDescriptionDraft,
      });
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("style_description_updated"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${tRef.current("update_failed")}${(err as Error).message}`, "error");
    } finally {
      setSavingStyleDescription(false);
    }
  }, [projectName, refreshProject, styleDescriptionDraft]);

  // If no overview has been generated yet, show the Welcome/Setup screen
  if (projectData && !projectData.overview?.synopsis && !projectData.overview?.genre) {
    return (
      <WelcomeCanvas
        projectName={projectName}
        projectTitle={projectData.title}
        onUpload={handleUpload}
        onAnalyze={handleAnalyze}
      />
    );
  }

  const episodes = projectData?.episodes ?? [];

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-gray-950 p-8">
      <div className="mx-auto grid w-full max-w-6xl grid-cols-1 gap-8 lg:grid-cols-12">
        {/* Left Column: Core Overview (8 units) */}
        <div className="space-y-8 lg:col-span-8">
          {/* Header */}
          <div className="flex items-end justify-between border-b border-gray-800 pb-6">
            <div>
              <h1 className="text-3xl font-bold text-gray-100">
                {projectData?.title || projectName}
              </h1>
              <p className="mt-2 text-sm text-gray-500">
                {t("project_details")} · {t(`dashboard:${projectData?.content_mode === "narration" ? "narration" : "standard"}`)}
              </p>
            </div>
            <button
              type="button"
              disabled={regenerating}
              onClick={handleRegenerate}
              className="flex items-center gap-2 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-gray-300 transition hover:bg-gray-800 hover:text-white disabled:opacity-50"
            >
              <RefreshCw
                className={`h-4 w-4 ${regenerating ? "animate-spin" : ""}`}
              />
              {regenerating ? t("regenerating") : t("regenerate_overview")}
            </button>
          </div>

          {/* Synopsis */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              {t("synopsis")}
            </h2>
            <p className="mt-4 text-sm leading-relaxed text-gray-300">
              {projectData?.overview?.synopsis || t("no_description")}
            </p>
          </section>

          {/* Metadata Grid */}
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
            <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("genre")}
              </h2>
              <p className="mt-3 text-sm font-medium text-gray-200">
                {projectData?.overview?.genre || t("no_description")}
              </p>
            </section>
            <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("theme")}
              </h2>
              <p className="mt-3 text-sm font-medium text-gray-200">
                {projectData?.overview?.theme || t("no_description")}
              </p>
            </section>
          </div>

          {/* World Setting */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              {t("world_setting")}
            </h2>
            <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-gray-300">
              {projectData?.overview?.world_setting || t("no_description")}
            </p>
          </section>

          {/* Cost Estimates */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("estimated_cost")}
              </h2>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-indigo-500" />
                  <span className="text-[10px] text-gray-500 uppercase">{t("text_generation")}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  <span className="text-[10px] text-gray-500 uppercase">{t("image_generation")}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-amber-500" />
                  <span className="text-[10px] text-gray-500 uppercase">{t("video_generation")}</span>
                </div>
              </div>
            </div>

            {costLoading && !projectTotals ? (
              <div className="flex h-32 items-center justify-center gap-3 text-gray-500">
                <RefreshCw className="h-4 w-4 animate-spin text-indigo-500" />
                <span className="text-sm">{t("common:loading")}</span>
              </div>
            ) : costError && !projectTotals ? (
              <div className="flex h-32 items-center justify-center text-sm text-rose-400">
                {costError}
              </div>
            ) : projectTotals ? (
              <div className="space-y-8">
                {/* Project Totals */}
                <div className="tabular-nums">
                  <div className="mb-3 text-sm font-semibold text-gray-300">{t("project_total_cost")}</div>
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

                {/* Per Episode Estimates */}
                <div>
                  <h3 className="mb-4 text-xs font-medium text-gray-500 uppercase tracking-widest">
                    {t("per_episode")}
                  </h3>
                  <div className="space-y-3">
                    {episodes.map((ep) => {
                      const epCost = getEpisodeCost(ep.episode);
                      if (!epCost) return null;
                      return (
                        <div
                          key={ep.episode}
                          className="flex flex-wrap items-center gap-3 rounded-xl bg-gray-950/50 px-4 py-3 tabular-nums"
                        >
                          <span className="font-mono text-xs text-gray-400">E{ep.episode}</span>
                          <span className="text-sm text-gray-200">{ep.title}</span>
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
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex h-32 items-center justify-center text-sm text-gray-600">
                {t("no_usage_data")}
              </div>
            )}
          </section>
        </div>

        {/* Right Column: Visual Style & Assets (4 units) */}
        <div className="space-y-8 lg:col-span-4">
          {/* Visual Style Image */}
          <section>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("style_reference_image")}
              </h2>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => styleInputRef.current?.click()}
                  disabled={uploadingStyleImage}
                  className="rounded-md p-1.5 text-gray-500 transition hover:bg-gray-800 hover:text-white"
                  title={t("upload_style_image")}
                >
                  {uploadingStyleImage ? (
                    <RefreshCw className="h-4 w-4 animate-spin" />
                  ) : (
                    <Upload className="h-4 w-4" />
                  )}
                </button>
                {projectData?.style_image && (
                  <button
                    type="button"
                    onClick={handleDeleteStyleImage}
                    disabled={deletingStyleImage}
                    className="rounded-md p-1.5 text-gray-500 transition hover:bg-gray-800 hover:text-red-400"
                    title={t("delete_style_image")}
                  >
                    {deletingStyleImage ? (
                      <RefreshCw className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                  </button>
                )}
              </div>
              <input
                ref={styleInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleStyleImageChange}
              />
            </div>

            <div className="aspect-[4/3] overflow-hidden rounded-2xl border border-gray-800 bg-gray-900">
              {projectData?.style_image ? (
                <PreviewableImageFrame
                  src={API.getFileUrl(projectName, projectData.style_image, styleImageFp)}
                  alt={t("visual_style_reference")}
                >
                  <img
                    src={API.getFileUrl(projectName, projectData.style_image, styleImageFp)}
                    alt={t("visual_style_reference")}
                    className="h-full w-full object-cover"
                  />
                </PreviewableImageFrame>
              ) : (
                <button
                  type="button"
                  onClick={() => styleInputRef.current?.click()}
                  className="group flex h-full w-full flex-col items-center justify-center gap-3 text-gray-600 transition hover:bg-gray-800/50"
                >
                  <div className="rounded-full bg-gray-800 p-4 transition group-hover:scale-110 group-hover:bg-gray-700 group-hover:text-gray-400">
                    <ImagePlus className="h-8 w-8" />
                  </div>
                  <span className="text-xs font-medium tracking-wide">
                    {t("upload_reference")}
                  </span>
                </button>
              )}
            </div>
          </section>

          {/* Style Description (Editable) */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              {t("style_description")}
            </h2>
            <div className="mt-4">
              <textarea
                value={styleDescriptionDraft}
                onChange={(e) => setStyleDescriptionDraft(e.target.value)}
                placeholder={t("style_desc_placeholder")}
                className="h-32 w-full resize-none rounded-lg border border-transparent bg-transparent text-sm leading-relaxed text-gray-300 placeholder-gray-600 outline-none transition focus:border-indigo-500/30"
              />
              {styleDescriptionDraft !== (projectData?.style_description ?? "") && (
                <div className="mt-3 flex justify-end">
                  <button
                    type="button"
                    disabled={savingStyleDescription}
                    onClick={handleSaveStyleDescription}
                    className="rounded-lg bg-indigo-600/10 px-3 py-1.5 text-xs font-semibold text-indigo-400 transition hover:bg-indigo-600/20"
                  >
                    {savingStyleDescription ? t("common:saving") : t("update_style_description")}
                  </button>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
