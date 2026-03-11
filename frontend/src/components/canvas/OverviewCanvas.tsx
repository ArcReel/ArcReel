import { useCallback, useEffect, useRef, useState } from "react";
import { ImagePlus, RefreshCw, Trash2, Upload } from "lucide-react";
import type { ProjectData } from "@/types";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { buildEntityRevisionKey } from "@/utils/project-changes";
import { WelcomeCanvas } from "./WelcomeCanvas";

interface OverviewCanvasProps {
  projectName: string;
  projectData: ProjectData | null;
}

export function OverviewCanvas({ projectName, projectData }: OverviewCanvasProps) {
  const projectRevisionKey = buildEntityRevisionKey("project", "project");
  const mediaRevision = useAppStore((s) => s.getEntityRevision(projectRevisionKey));
  const [regenerating, setRegenerating] = useState(false);
  const [uploadingStyleImage, setUploadingStyleImage] = useState(false);
  const [deletingStyleImage, setDeletingStyleImage] = useState(false);
  const [savingStyleDescription, setSavingStyleDescription] = useState(false);
  const [styleDescriptionDraft, setStyleDescriptionDraft] = useState(
    projectData?.style_description ?? "",
  );
  const styleInputRef = useRef<HTMLInputElement>(null);

  const refreshProject = useCallback(
    async (invalidateProjectMedia: boolean = false) => {
      const res = await API.getProject(projectName);
      useProjectsStore.getState().setCurrentProject(
        projectName,
        res.project,
        res.scripts ?? {},
      );
      if (invalidateProjectMedia) {
        useAppStore.getState().invalidateEntities([projectRevisionKey]);
      }
    },
    [projectName, projectRevisionKey],
  );

  useEffect(() => {
    setStyleDescriptionDraft(projectData?.style_description ?? "");
  }, [projectData?.style_description]);

  const handleUpload = useCallback(
    async (file: File) => {
      await API.uploadFile(projectName, "source", file);
      useAppStore.getState().pushToast(`源文件 "${file.name}" 上传成功`, "success");
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
      useAppStore.getState().pushToast("项目概述已重新生成", "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`重新生成失败: ${(err as Error).message}`, "error");
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
        await refreshProject(true);
        useAppStore.getState().pushToast("风格参考图已更新", "success");
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(`上传失败: ${(err as Error).message}`, "error");
      } finally {
        setUploadingStyleImage(false);
      }
    },
    [projectName, refreshProject],
  );

  const handleDeleteStyleImage = useCallback(async () => {
    if (deletingStyleImage || !projectData?.style_image) return;
    if (!confirm("确定删除当前风格参考图吗？")) return;

    setDeletingStyleImage(true);
    try {
      await API.deleteStyleImage(projectName);
      await refreshProject(true);
      useAppStore.getState().pushToast("风格参考图已删除", "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`删除失败: ${(err as Error).message}`, "error");
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
      useAppStore.getState().pushToast("风格描述已保存", "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`保存失败: ${(err as Error).message}`, "error");
    } finally {
      setSavingStyleDescription(false);
    }
  }, [projectName, refreshProject, savingStyleDescription, styleDescriptionDraft]);

  if (!projectData) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        加载项目数据中...
      </div>
    );
  }

  const status = projectData.status;
  const overview = projectData.overview;
  const styleImageUrl = projectData.style_image
    ? API.getFileUrl(projectName, projectData.style_image, mediaRevision)
    : null;
  const styleDescriptionDirty =
    styleDescriptionDraft !== (projectData.style_description ?? "");
  const showWelcome = !overview && (projectData.episodes?.length ?? 0) === 0;
  const projectStyleCard = (
    <section className="rounded-2xl border border-gray-800 bg-gray-900/90 p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-gray-200">项目风格</h3>
          <p className="max-w-2xl text-xs leading-5 text-gray-500">
            参考图会参与后续画面生成；风格描述用于补充视觉规则，校准整体调性、材质和镜头气质。
          </p>
        </div>
        <div className="inline-flex items-center rounded-full border border-gray-700 bg-gray-800 px-3 py-1 text-xs text-gray-300">
          {projectData.style || "未设置风格标签"}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
        <div className="space-y-3">
          {styleImageUrl ? (
            <PreviewableImageFrame src={styleImageUrl} alt="项目风格参考图">
              <div className="overflow-hidden rounded-xl border border-gray-700 bg-gray-950/70">
                <img
                  src={styleImageUrl}
                  alt="项目风格参考图"
                  className="aspect-[4/3] w-full object-cover"
                />
              </div>
            </PreviewableImageFrame>
          ) : (
            <button
              type="button"
              onClick={() => styleInputRef.current?.click()}
              disabled={uploadingStyleImage}
              className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-gray-700 bg-gray-950/40 px-4 text-sm text-gray-500 transition-colors hover:border-gray-500 hover:text-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Upload className="h-4 w-4" />
              <span>{uploadingStyleImage ? "上传中..." : "上传风格参考图"}</span>
              <span className="text-xs text-gray-600">支持 PNG / JPG / WEBP</span>
            </button>
          )}

          <div className="rounded-xl border border-gray-800 bg-gray-950/40 p-3">
            <p className="text-xs font-medium text-gray-400">使用说明</p>
            <p className="mt-1 text-sm leading-6 text-gray-300">
              {styleImageUrl
                ? "当前参考图会作为统一视觉基线，用于角色图、分镜图和视频生成。"
                : "还没有绑定项目级参考图，可以先上传一张目标风格样片作为统一基线。"}
            </p>

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => styleInputRef.current?.click()}
                disabled={uploadingStyleImage}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-300 transition-colors hover:border-gray-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ImagePlus className="h-4 w-4" />
                {styleImageUrl ? "替换参考图" : "上传参考图"}
              </button>
              {styleImageUrl && (
                <button
                  type="button"
                  onClick={() => void handleDeleteStyleImage()}
                  disabled={deletingStyleImage}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/30 px-3 py-2 text-sm text-red-300 transition-colors hover:border-red-400/50 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" />
                  {deletingStyleImage ? "删除中..." : "删除参考图"}
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
          />
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-950/35 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="text-xs font-medium text-gray-400">风格描述</label>
            <span className="text-[11px] text-gray-600">
              {styleDescriptionDraft.trim().length} 字
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            上传参考图后系统会自动分析并填充风格描述；你也可以继续手动校准。
          </p>

          <textarea
            value={styleDescriptionDraft}
            onChange={(e) => setStyleDescriptionDraft(e.target.value)}
            rows={8}
            className="mt-3 min-h-44 w-full rounded-xl border border-gray-700 bg-gray-800/80 px-4 py-3 text-sm leading-relaxed text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="上传风格参考图后，系统会自动分析并填充风格描述；也可以手动编辑。"
          />

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs leading-5 text-gray-500">
              {styleImageUrl
                ? "建议把风格描述用于补充光线、色彩、材质与镜头语言。"
                : "没有参考图时，也可以先用文字明确画面风格和审美约束。"}
            </p>
            {styleDescriptionDirty && (
              <button
                type="button"
                onClick={() => void handleSaveStyleDescription()}
                disabled={savingStyleDescription}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {savingStyleDescription ? "保存中..." : "保存风格描述"}
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
              ? "说书+画面模式"
              : "剧集动画模式"}{" "}
            · {projectData.style || "未设置风格"}
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
                  <h3 className="text-sm font-semibold text-gray-300">项目概述</h3>
                  <button
                    type="button"
                    onClick={() => void handleRegenerate()}
                    disabled={regenerating}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
                    title="重新生成概述"
                  >
                    <RefreshCw
                      className={`h-3 w-3 ${regenerating ? "animate-spin" : ""}`}
                    />
                    <span>{regenerating ? "生成中..." : "重新生成"}</span>
                  </button>
                </div>
                <p className="text-sm text-gray-400">{overview.synopsis}</p>
                <div className="flex gap-4 text-xs text-gray-500">
                  <span>题材: {overview.genre}</span>
                  <span>主题: {overview.theme}</span>
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
                      characters: "角色",
                      clues: "线索",
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
                        <div className="h-1.5 overflow-hidden rounded-full bg-gray-800">
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

            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-gray-300">剧集</h3>
              {(projectData.episodes?.length ?? 0) === 0 ? (
                <p className="text-sm text-gray-500">
                  暂无剧集。使用 AI 助手生成剧本。
                </p>
              ) : (
                (projectData.episodes ?? []).map((ep) => (
                  <div
                    key={ep.episode}
                    className="flex items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 px-4 py-2.5"
                  >
                    <span className="font-mono text-xs text-gray-400">
                      E{ep.episode}
                    </span>
                    <span className="text-sm text-gray-200">{ep.title}</span>
                    <span className="ml-auto text-xs text-gray-500">
                      {ep.scenes_count ?? "?"} 片段 · {ep.status ?? "draft"}
                    </span>
                  </div>
                ))
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
