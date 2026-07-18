import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { errMsg, voidPromise } from "@/utils/async";
import { Route, Switch, Redirect } from "wouter";
import { useTranslation } from "react-i18next";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./OverviewCanvas";
import { SourceFileViewer } from "./SourceFileViewer";
import { SourceFilesPage } from "./SourceFilesPage";
import { CharactersPage } from "./lorebook/CharactersPage";
import { ScenesPage } from "./lorebook/ScenesPage";
import { PropsPage } from "./lorebook/PropsPage";
import { ProductsPage } from "./lorebook/ProductsPage";
import { ReferenceVideoCanvas } from "./reference/ReferenceVideoCanvas";
import { GridImageToVideoCanvas } from "./grid/GridImageToVideoCanvas";
import { EpisodeSourceReview } from "./EpisodeSourceReview";
import { API } from "@/api";
import {
  enqueueCharacter,
  enqueueEpisodeNarration,
  enqueueGrid,
  enqueueNarration,
  enqueueProduct,
  enqueueProp,
  enqueueScene,
  enqueueStoryboard,
  enqueueVideo,
} from "@/actions/generation";
import { buildEntityRevisionKey } from "@/utils/project-changes";
import { getProviderModels, getCustomProviderModels, lookupSupportedDurations } from "@/utils/provider-models";
import { effectiveMode } from "@/utils/generation-mode";
import type { Scene, Prop, Product, CustomProviderInfo, ProviderInfo } from "@/types";
import type { EpisodeScript } from "@/types/script";
import { REFERENCE_SHOT_DURATION_OPTIONS } from "@/types/script";

// ---------------------------------------------------------------------------
// resolveSegmentPrompt -- shared segment lookup for generate storyboard/video
// ---------------------------------------------------------------------------

type PromptField = "image_prompt" | "video_prompt";

function resolveSegmentPrompt(
  scripts: Record<string, EpisodeScript>,
  segmentId: string,
  field: PromptField,
  scriptFile?: string,
): { resolvedFile: string; prompt: unknown; duration: number } | null {
  const resolvedFile = scriptFile ?? Object.keys(scripts)[0];
  if (!resolvedFile) return null;
  const script = scripts[resolvedFile];
  if (!script) return null;
  const seg =
    script.content_mode === "narration"
      ? script.segments.find((s) => s.segment_id === segmentId)
      : script.content_mode === "ad"
        ? script.shots.find((s) => s.shot_id === segmentId)
        : script.scenes.find((s) => s.scene_id === segmentId);
  return {
    resolvedFile,
    prompt: seg?.[field] ?? "",
    duration: seg?.duration_seconds ?? 4,
  };
}

// ---------------------------------------------------------------------------
// StudioCanvasRouter -- reads Zustand store data and renders the correct
// canvas view based on the nested route within /app/projects/:projectName.
// ---------------------------------------------------------------------------

export function StudioCanvasRouter() {
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  // eslint-disable-next-line react-hooks/refs -- tRef 是稳定 event-handler ref 模式，用于在回调中获取最新 t 而不触发无限 useCallback 重建
  tRef.current = t;
  const { currentProjectData, currentProjectName, currentScripts } =
    useProjectsStore();

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [globalVideoBackend, setGlobalVideoBackend] = useState("");
  const [resolvedDurationOptions, setResolvedDurationOptions] = useState<
    number[] | undefined
  >(undefined);

  useEffect(() => {
    let disposed = false;
    Promise.all([getProviderModels(), getCustomProviderModels(), API.getSystemConfig()]).then(
      ([provList, customList, configRes]) => {
        if (disposed) return;
        setProviders(provList);
        setCustomProviders(customList);
        setGlobalVideoBackend(configRes.settings?.default_video_backend ?? "");
      },
    ).catch(() => {});
    return () => { disposed = true; };
  }, []);

  // 已配置 backend 时本地 lookup 即可（同步、零延迟）；未配置时调后端
  // /video-capabilities，让 ConfigResolver 自动 fallback 到 PROVIDER_REGISTRY
  // 第一个 ready 的 default video model（与生成路径用同一套规则，避免 FE/BE 漂移）。
  const localDurationOptions = useMemo(() => {
    const backend = currentProjectData?.video_backend || globalVideoBackend;
    if (!backend) return undefined;
    return lookupSupportedDurations(providers, backend, customProviders);
  }, [providers, customProviders, globalVideoBackend, currentProjectData?.video_backend]);

  useEffect(() => {
    // 依赖变化时清理旧的 resolved 选项；本地 lookup 有结果或缺项目名时同步清零，
    // 否则在异步拉取新项目的 /video-capabilities 之前先 reset 以避免沿用旧值。
    if (localDurationOptions !== undefined) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResolvedDurationOptions(undefined);
      return;
    }
    if (!currentProjectName) {
      setResolvedDurationOptions(undefined);
      return;
    }
    setResolvedDurationOptions(undefined);
    let disposed = false;
    API.getVideoCapabilities(currentProjectName)
      .then((caps) => {
        if (disposed) return;
        setResolvedDurationOptions(caps.supported_durations);
      })
      .catch(() => {
        if (disposed) return;
        setResolvedDurationOptions(undefined);
      });
    return () => {
      disposed = true;
    };
  }, [currentProjectName, localDurationOptions]);

  const durationOptions = localDurationOptions ?? resolvedDurationOptions;

  // 从任务队列派生 loading 状态（替代本地 state）：活跃 + 最新行胜出两条不变量下沉到 store selector
  const generatingCharacterNames = useActiveResourceIds("character", currentProjectName);
  const generatingSceneNames = useActiveResourceIds("scene", currentProjectName);
  const generatingPropNames = useActiveResourceIds("prop", currentProjectName);
  const generatingProductNames = useActiveResourceIds("product", currentProjectName);

  // 刷新项目数据；返回本地 store 是否已同步成功，供调用方决定是否推进依赖新顺序的 UI 状态。
  // 在途合并 + 失败留旧收敛于 projects-store 的 refreshProject，此处仅表达意图。
  const refreshProject = useCallback(
    (invalidateKeys: string[] = []): Promise<boolean> =>
      currentProjectName
        ? useProjectsStore.getState().refreshProject(currentProjectName, { invalidateKeys })
        : Promise.resolve(false),
    [currentProjectName],
  );

  // ---- Timeline action callbacks ----
  // These receive scriptFile from TimelineCanvas so they always use the active episode's script.
  const handleUpdatePrompt = useCallback(async (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
    scriptFile?: string,
  ) => {
    if (!currentProjectName) return;
    const mode = currentProjectData?.content_mode ?? "narration";
    const patch =
      typeof fieldOrPatch === "string"
        ? { [fieldOrPatch]: value }
        : fieldOrPatch;
    try {
      if (mode === "ad") {
        await API.updateShot(currentProjectName, segmentId, scriptFile ?? "", patch);
      } else if (mode === "drama") {
        await API.updateScene(currentProjectName, segmentId, scriptFile ?? "", patch);
      } else {
        await API.updateSegment(currentProjectName, segmentId, { script_file: scriptFile, ...patch });
      }
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_prompt_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentProjectData, refreshProject]);

  // ad 镜头重排：把目标镜头向前/向后移动一位，提交整列全排列。
  // 返回是否移动成功，供编辑器把选中态跟随到镜头的新位置。
  const handleMoveShot = useCallback(async (
    shotId: string,
    direction: "earlier" | "later",
    scriptFile?: string,
  ): Promise<boolean> => {
    if (!currentProjectName || !currentScripts) return false;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return false;
    const script = currentScripts[resolvedFile];
    if (!script || script.content_mode !== "ad") return false;
    const ids = script.shots.map((s) => s.shot_id);
    const index = ids.indexOf(shotId);
    const target = direction === "earlier" ? index - 1 : index + 1;
    if (index === -1 || target < 0 || target >= ids.length) return false;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    try {
      await API.reorderShots(currentProjectName, resolvedFile, ids);
      // 仅在本地 store 已写回新顺序时报告成功：刷新失败时 segments 仍是旧序，
      // 此时推进 selectedIndex 会让详情面板静默切到相邻镜头。
      return await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("reorder_shot_failed", { message: errMsg(err) }), "error");
      return false;
    }
  }, [currentProjectName, currentScripts, refreshProject]);

  const handleUpdateEpisodeTitle = useCallback(async (episode: number, title: string) => {
    if (!currentProjectName) return;
    try {
      await API.updateEpisode(currentProjectName, episode, { title });
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("episode_title_updated"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("episode_title_update_failed", { message: errMsg(err) }), "error");
      throw err; // 让 EditableEpisodeTitle 保持编辑态，不误清空
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateStoryboard = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolved = resolveSegmentPrompt(currentScripts, segmentId, "image_prompt", scriptFile);
    if (!resolved) return;
    try {
      await enqueueStoryboard(
        currentProjectName,
        segmentId,
        resolved.prompt as string | Record<string, unknown>,
        resolved.resolvedFile,
      );
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("generate_storyboard_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentScripts]);

  const handleGenerateVideo = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolved = resolveSegmentPrompt(currentScripts, segmentId, "video_prompt", scriptFile);
    if (!resolved) return;
    try {
      await enqueueVideo(
        currentProjectName,
        segmentId,
        resolved.prompt as string | Record<string, unknown>,
        resolved.resolvedFile,
        resolved.duration,
      );
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("generate_video_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentScripts]);

  // 未配置 audio 供应商时在前端就给出清晰提示（后端入队前还有同语义的 400 兜底）
  const ensureAudioProviderConfigured = useCallback((): boolean => {
    const cfg = useConfigStatusStore.getState();
    if (cfg.initialized && !cfg.hasMediaType("audio")) {
      useAppStore.getState().pushToast(tRef.current("audio_provider_not_configured_toast"), "error");
      return false;
    }
    return true;
  }, []);

  const handleGenerateNarration = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    if (!ensureAudioProviderConfigured()) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    try {
      await enqueueNarration(currentProjectName, segmentId, resolvedFile);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("generate_narration_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentScripts, ensureAudioProviderConfigured]);

  const handleGenerateEpisodeNarration = useCallback(async (scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    if (!ensureAudioProviderConfigured()) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    try {
      await enqueueEpisodeNarration(currentProjectName, resolvedFile);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("generate_narration_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentScripts, ensureAudioProviderConfigured]);

  // ---- Character CRUD callbacks ----
  const handleSaveCharacter = useCallback(async (
    name: string,
    payload: {
      description: string;
      voiceStyle: string;
      referenceFile?: File | null;
    },
  ) => {
    if (!currentProjectName) return;
    try {
      await API.updateCharacter(currentProjectName, name, {
        description: payload.description,
        voice_style: payload.voiceStyle,
      });

      if (payload.referenceFile) {
        await API.uploadFile(
          currentProjectName,
          "character_ref",
          payload.referenceFile,
          name,
        );
      }

      await refreshProject(
        payload.referenceFile
          ? [buildEntityRevisionKey("character", name)]
          : [],
      );
      useAppStore.getState().pushToast(tRef.current("character_updated_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_character_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateCharacter = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueCharacter(
        currentProjectName,
        name,
        currentProjectData?.characters?.[name]?.description ?? "",
      );
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddCharacterSubmit = useCallback(async (
    name: string,
    description: string,
    voiceStyle: string,
    referenceFile?: File | null,
  ) => {
    if (!currentProjectName) return;
    try {
      await API.addCharacter(currentProjectName, name, description, voiceStyle);

      if (referenceFile) {
        await API.uploadFile(currentProjectName, "character_ref", referenceFile, name);
      }

      await refreshProject(
        referenceFile
          ? [buildEntityRevisionKey("character", name)]
          : [],
      );
      useAppStore.getState().pushToast(tRef.current("character_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: errMsg(err) }), "error");
      throw err; // AssetFormModal onSubmit 消费：失败时阻止 setAdding(false) 关闭对话框
    }
  }, [currentProjectName, refreshProject]);

  // ---- Scene CRUD callbacks ----
  const handleUpdateScene = useCallback(async (name: string, updates: Partial<Scene>) => {
    if (!currentProjectName) return;
    try {
      await API.updateProjectScene(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_scene_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateScene = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueScene(currentProjectName, name, currentProjectData?.scenes?.[name]?.description ?? "");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddSceneSubmit = useCallback(async (name: string, description: string) => {
    if (!currentProjectName) return;
    try {
      await API.addProjectScene(currentProjectName, name, description);
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("scene_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: errMsg(err) }), "error");
      throw err; // AssetFormModal onSubmit 消费：失败时阻止 setAdding(false) 关闭对话框
    }
  }, [currentProjectName, refreshProject]);

  // ---- Prop CRUD callbacks ----
  const handleUpdateProp = useCallback(async (name: string, updates: Partial<Prop>) => {
    if (!currentProjectName) return;
    try {
      await API.updateProjectProp(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_prop_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateProp = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueProp(currentProjectName, name, currentProjectData?.props?.[name]?.description ?? "");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddPropSubmit = useCallback(async (name: string, description: string) => {
    if (!currentProjectName) return;
    try {
      await API.addProjectProp(currentProjectName, name, description);
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("prop_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: errMsg(err) }), "error");
      throw err; // AssetFormModal onSubmit 消费：失败时阻止 setAdding(false) 关闭对话框
    }
  }, [currentProjectName, refreshProject]);

  // ---- Product CRUD callbacks ----
  const handleUpdateProduct = useCallback(async (name: string, updates: Partial<Product>) => {
    if (!currentProjectName) return;
    try {
      await API.updateProjectProduct(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_product_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateProduct = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueProduct(
        currentProjectName,
        name,
        currentProjectData?.products?.[name]?.description ?? "",
      );
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddProductSubmit = useCallback(async (name: string, description: string, brand: string) => {
    if (!currentProjectName) return;
    try {
      await API.addProjectProduct(currentProjectName, name, description, brand || undefined);
      await refreshProject();
      useAppStore.getState().pushToast(tRef.current("product_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: errMsg(err) }), "error");
      throw err; // ProductFormModal onSubmit 消费：失败时阻止关闭对话框
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateGrid = useCallback(async (episode: number, scriptFile: string, sceneIds?: string[]) => {
    if (!currentProjectName) return;
    try {
      await enqueueGrid(currentProjectName, episode, scriptFile, sceneIds);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("grid_generation_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName]);

  const handleRestoreAsset = useCallback(async () => {
    await refreshProject();
  }, [refreshProject]);

  const handleGenerateCharacterVoid = useCallback((...args: Parameters<typeof handleGenerateCharacter>) => {
    void handleGenerateCharacter(...args).catch(console.error);
  }, [handleGenerateCharacter]);
  const handleUpdateSceneVoid = useCallback((...args: Parameters<typeof handleUpdateScene>) => {
    void handleUpdateScene(...args).catch(console.error);
  }, [handleUpdateScene]);
  const handleGenerateSceneVoid = useCallback((...args: Parameters<typeof handleGenerateScene>) => {
    void handleGenerateScene(...args).catch(console.error);
  }, [handleGenerateScene]);
  const handleUpdatePropVoid = useCallback((...args: Parameters<typeof handleUpdateProp>) => {
    void handleUpdateProp(...args).catch(console.error);
  }, [handleUpdateProp]);
  const handleGeneratePropVoid = useCallback((...args: Parameters<typeof handleGenerateProp>) => {
    void handleGenerateProp(...args).catch(console.error);
  }, [handleGenerateProp]);
  const handleUpdateProductVoid = useCallback((...args: Parameters<typeof handleUpdateProduct>) => {
    void handleUpdateProduct(...args).catch(console.error);
  }, [handleUpdateProduct]);
  const handleGenerateProductVoid = useCallback((...args: Parameters<typeof handleGenerateProduct>) => {
    void handleGenerateProduct(...args).catch(console.error);
  }, [handleGenerateProduct]);

  if (!currentProjectName) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        {t("loading_placeholder")}
      </div>
    );
  }

  return (
    <Switch>
      <Route path="/">
        <OverviewCanvas
          projectName={currentProjectName}
          projectData={currentProjectData}
        />
      </Route>

      <Route path="/lorebook">
        <Redirect to="/characters" />
      </Route>

      <Route path="/clues">
        <Redirect to="/scenes" />
      </Route>

      <Route path="/source">
        <SourceFilesPage projectName={currentProjectName} />
      </Route>

      <Route path="/characters">
        <CharactersPage
          projectName={currentProjectName}
          characters={currentProjectData?.characters ?? {}}
          onSaveCharacter={handleSaveCharacter}
          onGenerateCharacter={handleGenerateCharacterVoid}
          onAddCharacter={handleAddCharacterSubmit}
          onRestoreCharacterVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingCharacterNames={generatingCharacterNames}
        />
      </Route>

      <Route path="/scenes">
        <ScenesPage
          projectName={currentProjectName}
          scenes={currentProjectData?.scenes ?? {}}
          onUpdateScene={handleUpdateSceneVoid}
          onGenerateScene={handleGenerateSceneVoid}
          onAddScene={handleAddSceneSubmit}
          onRestoreSceneVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingSceneNames={generatingSceneNames}
        />
      </Route>

      <Route path="/props">
        <PropsPage
          projectName={currentProjectName}
          props={currentProjectData?.props ?? {}}
          onUpdateProp={handleUpdatePropVoid}
          onGenerateProp={handleGeneratePropVoid}
          onAddProp={handleAddPropSubmit}
          onRestorePropVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingPropNames={generatingPropNames}
        />
      </Route>

      <Route path="/products">
        <ProductsPage
          projectName={currentProjectName}
          products={currentProjectData?.products ?? {}}
          onUpdateProduct={handleUpdateProductVoid}
          onGenerateProduct={handleGenerateProductVoid}
          onAddProduct={handleAddProductSubmit}
          onRestoreProductVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingProductNames={generatingProductNames}
        />
      </Route>

      <Route path="/source/:filename">
        {(params) => (
          <SourceFileViewer
            projectName={currentProjectName}
            filename={decodeURIComponent(params.filename)}
          />
        )}
      </Route>

      <Route path="/episodes/:episodeId">
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find((e) => e.episode === epNum);
          const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
          const script = scriptFile ? (currentScripts[scriptFile] ?? null) : null;
          const mode = effectiveMode(currentProjectData, episode);
          const hasDraft =
            episode?.script_status === "segmented" || episode?.script_status === "generated";
          // ad 剧本骨架唯一（shots[]），两条生成路径都进镜头编辑画布：
          // reference_video 路径的派生分组画布尚未落地，先共用 Timeline 编辑器；
          // 该路径下镜头时长为 1-15 秒自由整数，不取供应商 supported_durations，
          // 且不暴露逐镜头图生视频入口（该路径按 ADR 0033 跳过分镜步骤，
          // 入队也会被执行层 supported_durations 校验拒绝）。
          const isAd = currentProjectData?.content_mode === "ad";
          const adReference = isAd && mode === "reference_video";
          const effectiveDurationOptions = adReference
            ? REFERENCE_SHOT_DURATION_OPTIONS
            : durationOptions;

          // 已选集但剧本未生成：进入切片审阅视图（narration/drama 全部生成路径——
          // reference_video 此时 units 为空，同样没有可展示内容）；ad 恒单集无源文
          // 切片，走各自画布。
          const showSourceReview = Boolean(episode) && !script && !hasDraft && !isAd;

          return (
            <div className="flex h-full flex-col">
              <div className="min-h-0 flex-1">
                {showSourceReview && episode ? (
                  <EpisodeSourceReview
                    projectName={currentProjectName}
                    episode={epNum}
                    episodes={currentProjectData?.episodes ?? []}
                  />
                ) : mode === "reference_video" && !isAd ? (
                  <ReferenceVideoCanvas
                    // 同一 epNum 跨项目不 remount 会让 optimisticUnitIds / prevTaskStatusRef
                    // 残留上个项目的状态（例如 "E1U1" 长驻 set 里），切到同名 unit 的新项目
                    // 时 "optimistic && !hasQueueRow" 会误判 busy。改 key 到 project::episode
                    // 让实例天然按项目隔离，避免显式 pruning 逻辑。
                    key={`${currentProjectName}::${epNum}`}
                    projectName={currentProjectName}
                    episode={epNum}
                    episodeTitle={episode?.title}
                    onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
                    canEditTitle={Boolean(episode?.script_file)}
                    hasScript={Boolean(script)}
                  />
                ) : mode === "grid" ? (
                  <GridImageToVideoCanvas
                    key={`${currentProjectName}::${epNum}`}
                    projectName={currentProjectName}
                    episode={epNum}
                    episodeTitle={episode?.title}
                    onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
                    canEditTitle={Boolean(episode?.script_file)}
                    hasDraft={hasDraft}
                    episodeScript={script}
                    scriptFile={scriptFile ?? undefined}
                    projectData={currentProjectData}
                    durationOptions={durationOptions}
                    onUpdatePrompt={handleUpdatePrompt}
                    onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
                    onGenerateVideo={voidPromise(handleGenerateVideo)}
                    onGenerateNarration={voidPromise(handleGenerateNarration)}
                    onGenerateEpisodeNarration={voidPromise(handleGenerateEpisodeNarration)}
                    onGenerateGrid={handleGenerateGrid}
                    onRestoreStoryboard={handleRestoreAsset}
                    onRestoreVideo={handleRestoreAsset}
                  />
                ) : (
                  <TimelineCanvas
                    // 和 ReferenceVideoCanvas (上方) 同理：同 epNum 跨项目不 remount
                    // 会让 TimelineCanvas 内部的 useState / useRef（选中 scene、草稿缓冲、
                    // 滚动位置等）残留上一个项目的值。key 带上 projectName 天然按项目隔离。
                    key={`${currentProjectName}::${epNum}`}
                    projectName={currentProjectName}
                    episode={epNum}
                    episodeTitle={episode?.title}
                    onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
                    canEditTitle={Boolean(episode?.script_file)}
                    hasDraft={hasDraft}
                    episodeScript={script}
                    scriptFile={scriptFile ?? undefined}
                    projectData={currentProjectData}
                    durationOptions={effectiveDurationOptions}
                    onUpdatePrompt={handleUpdatePrompt}
                    onMoveShot={isAd ? handleMoveShot : undefined}
                    onGenerateStoryboard={adReference ? undefined : voidPromise(handleGenerateStoryboard)}
                    onGenerateVideo={adReference ? undefined : voidPromise(handleGenerateVideo)}
                    onGenerateNarration={voidPromise(handleGenerateNarration)}
                    onGenerateEpisodeNarration={voidPromise(handleGenerateEpisodeNarration)}
                    onRestoreStoryboard={handleRestoreAsset}
                    onRestoreVideo={handleRestoreAsset}
                  />
                )}
              </div>
            </div>
          );
        }}
      </Route>
    </Switch>
  );
}
