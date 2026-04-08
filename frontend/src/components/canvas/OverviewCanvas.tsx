
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
  const { t } = useTranslation();
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
      useAppStore.getState().pushToast(t("源文件 \"{name}\" 上传成功", { name: file.name }), "success");
    },
    [projectName, t],
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
      useAppStore.getState().pushToast(t("项目概述已重新生成"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${t("重新生成失败: ")}${(err as Error).message}`, "error");
    } finally {
      setRegenerating(false);
    }
  }, [projectName, refreshProject, t]);

  const handleStyleImageChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;

      setUploadingStyleImage(true);
      try {
        await API.uploadStyleImage(projectName, file);
        await refreshProject();
        useAppStore.getState().pushToast(t("风格参考图已更新"), "success");
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(`${t("上传失败: ")}${(err as Error).message}`, "error");
      } finally {
        setUploadingStyleImage(false);
      }
    },
    [projectName, refreshProject, t],
  );

  const handleDeleteStyleImage = useCallback(async () => {
    if (!confirm(t("确定要删除风格参考图吗？"))) return;
    setDeletingStyleImage(true);
    try {
      await API.deleteStyleImage(projectName);
      await refreshProject();
      useAppStore.getState().pushToast(t("风格参考图已删除"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${t("删除失败: ")}${(err as Error).message}`, "error");
    } finally {
      setDeletingStyleImage(false);
    }
  }, [projectName, refreshProject, t]);

  const handleSaveStyleDescription = useCallback(async () => {
    setSavingStyleDescription(true);
    try {
      await API.updateProject(projectName, {
        style_description: styleDescriptionDraft,
      });
      await refreshProject();
      useAppStore.getState().pushToast(t("风格描述已更新"), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`${t("更新失败: ")}${(err as Error).message}`, "error");
    } finally {
      setSavingStyleDescription(false);
    }
  }, [projectName, refreshProject, styleDescriptionDraft, t]);

  // If no overview has been generated yet, show the Welcome/Setup screen
  if (projectData && !projectData.synopsis && !projectData.genre) {
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
                {t("项目详情")} · {t(projectData?.content_mode === "narration" ? "narration" : "standard")}
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
              {regenerating ? t("正在重新生成...") : t("重新生成概述")}
            </button>
          </div>

          {/* Synopsis */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              {t("故事梗概")}
            </h2>
            <p className="mt-4 text-sm leading-relaxed text-gray-300">
              {projectData?.synopsis || t("暂无描述")}
            </p>
          </section>

          {/* Metadata Grid */}
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
            <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("题材")}
              </h2>
              <p className="mt-3 text-sm font-medium text-gray-200">
                {projectData?.genre || t("暂无描述")}
              </p>
            </section>
            <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("主题")}
              </h2>
              <p className="mt-3 text-sm font-medium text-gray-200">
                {projectData?.theme || t("暂无描述")}
              </p>
            </section>
          </div>

          {/* World Setting */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              {t("世界观设定")}
            </h2>
            <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-gray-300">
              {projectData?.world_setting || t("暂无描述")}
            </p>
          </section>

          {/* Cost Estimates */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                {t("预估费用")}
              </h2>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-indigo-500" />
                  <span className="text-[10px] text-gray-500 uppercase">{t("文本生成")}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  <span className="text-[10px] text-gray-500 uppercase">{t("图像生成")}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-amber-500" />
                  <span className="text-[10px] text-gray-500 uppercase">{t("视频生成")}</span>
                </div>
              </div>
            </div>

            {costLoading && !projectTotals ? (
              <div className="flex h-32 items-center justify-center gap-3 text-gray-500">
                <RefreshCw className="h-4 w-4 animate-spin text-indigo-500" />
                <span className="text-sm">{t("加载中...")}</span>
              </div>
            ) : costError && !projectTotals ? (
              <div className="flex h-32 items-center justify-center text-sm text-rose-400">
                {costError}
              </div>
            ) : projectTotals ? (
              <div className="space-y-8">
                {/* Total Cost Bar */}
                <div>
                  <div className="mb-2 flex items-end justify-between">
                    <span className="text-xs text-gray-400">{t("总计")}</span>
                    <span className="text-lg font-bold text-gray-100">
                      {formatCost(projectTotals.total)}
                    </span>
                  </div>
                  <div className="flex h-2.5 overflow-hidden rounded-full bg-gray-800">
                    <div
                      className="bg-indigo-500 transition-all duration-500"
                      style={{ width: `${(projectTotals.text / projectTotals.total) * 100}%` }}
                    />
                    <div
                      className="bg-emerald-500 transition-all duration-500"
                      style={{ width: `${(projectTotals.image / projectTotals.total) * 100}%` }}
                    />
                    <div
                      className="bg-amber-500 transition-all duration-500"
                      style={{ width: `${(projectTotals.video / projectTotals.total) * 100}%` }}
                    />
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-4">
                    {totalBreakdown(projectTotals).map((item) => (
                      <div key={item.label}>
                        <div className="text-[10px] text-gray-500 uppercase">{t(item.label)}</div>
                        <div className="text-sm font-medium text-gray-200">
                          {formatCost(item.value)}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Per Episode Estimates */}
                <div>
                  <h3 className="mb-4 text-xs font-medium text-gray-500 uppercase tracking-widest">
                    {t("分集统计")}
                  </h3>
                  <div className="space-y-3">
                    {episodes.map((ep) => {
                      const cost = getEpisodeCost(ep.episode);
                      if (!cost) return null;
                      return (
                        <div
                          key={ep.episode}
                          className="flex items-center justify-between rounded-xl bg-gray-950/50 px-4 py-3"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-medium text-gray-300 truncate">
                              E{ep.episode}: {ep.title}
                            </div>
                            <div className="mt-1 flex gap-3">
                              <span className="text-[10px] text-indigo-400/70">T: {formatCost(cost.text)}</span>
                              <span className="text-[10px] text-emerald-400/70">I: {formatCost(cost.image)}</span>
                              <span className="text-[10px] text-amber-400/70">V: {formatCost(cost.video)}</span>
                            </div>
                          </div>
                          <div className="ml-4 text-sm font-semibold text-gray-200">
                            {formatCost(cost.total)}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex h-32 items-center justify-center text-sm text-gray-600">
                {t("暂无用量数据")}
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
                {t("风格参考图")}
              </h2>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => styleInputRef.current?.click()}
                  disabled={uploadingStyleImage}
                  className="rounded-md p-1.5 text-gray-500 transition hover:bg-gray-800 hover:text-white"
                  title={t("上传风格图")}
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
                    title={t("删除风格图")}
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
                  src={projectData.style_image}
                  alt={t("视觉风格参考")}
                  fingerprint={styleImageFp}
                  className="h-full w-full object-cover"
                />
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
                    {t("上传参考图")}
                  </span>
                </button>
              )}
            </div>
          </section>

          {/* Style Description (Editable) */}
          <section className="rounded-2xl border border-gray-800 bg-gray-900/50 p-6">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
              {t("风格描述")}
            </h2>
            <div className="mt-4">
              <textarea
                value={styleDescriptionDraft}
                onChange={(e) => setStyleDescriptionDraft(e.target.value)}
                placeholder={t("风格描述描述")}
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
                    {savingStyleDescription ? t("保存中...") : t("更新风格描述")}
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
