import { stripScriptsPrefix } from "@/utils/task-target";
import { useCallback, useRef } from "react";
import { errMsg, voidPromise } from "@/utils/async";
import { Route, Switch, Redirect } from "wouter";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_EPISODES,
  WORKSPACE_ROUTE_FILES,
} from "@/app-routes";
import { useTranslation } from "react-i18next";
import { normalizeScriptFile, useProjectsStore } from "@/stores/projects-store";
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./overview/OverviewCanvas";
import { WorkspaceNotFound } from "./WorkspaceNotFound";
import { EpisodesView } from "./episodes/EpisodesView";
import { FileView } from "./files/FileView";
import { CharactersPage } from "./lorebook/CharactersPage";
import { ScenesPage } from "./lorebook/ScenesPage";
import { PropsPage } from "./lorebook/PropsPage";
import { ProductsPage } from "./lorebook/ProductsPage";
import { EpisodePage } from "./episode-page/EpisodePage";
import { LazyBoundary } from "@/components/shared/LazyBoundary";
import { lazyNamed } from "@/utils/lazy-component";
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
import {
  durationOutOfRangeReason,
  useModelCapabilities,
} from "@/hooks/useModelCapabilities";
import { gridStoryboardEnabled, normalizeRoute } from "@/utils/generation-mode";

const ReferenceVideoCanvas = lazyNamed(() => import("./reference/ReferenceVideoCanvas"), "ReferenceVideoCanvas");
const GridImageToVideoCanvas = lazyNamed(() => import("./grid/GridImageToVideoCanvas"), "GridImageToVideoCanvas");

const EPISODE_ROUTE_PATH = `/${WORKSPACE_ROUTE_EPISODES}/:episodeId`;


type PromptField = "image_prompt" | "video_prompt";

function resolveSegmentPrompt(
  projectName: string,
  episode: number,
  saving: ReadonlyMap<string, number>,
  segmentId: string,
  field: PromptField,
  scriptFile?: string,
): { resolvedFile: string; prompt: unknown; duration: number } | null {
  const { currentProjectName, currentProjectData, currentScripts, activeEpisodeScript } = useProjectsStore.getState();
  const bound = currentProjectData?.episodes.find((entry) => entry.episode === episode);
  const resolvedFile = normalizeScriptFile(bound?.script_file);
  if (currentProjectName !== projectName || !resolvedFile || (scriptFile && scriptFile !== resolvedFile)) return null;
  if (saving.has(JSON.stringify([projectName, resolvedFile]))) return null;
  if (!isDemoProject(projectName) && (
    activeEpisodeScript.status !== "ready" || activeEpisodeScript.projectName !== projectName ||
    activeEpisodeScript.episode !== episode || activeEpisodeScript.scriptFile !== resolvedFile
  )) return null;
  const script = currentScripts[resolvedFile];
  if (!script || script.episode !== episode) return null;
  if ("video_units" in script) return null;
  const seg =
    script.content_mode === "narration"
      ? script.segments.find((s) => s.segment_id === segmentId)
      : script.content_mode === "ad"
        ? script.shots.find((s) => s.shot_id === segmentId)
        : script.scenes.find((s) => s.scene_id === segmentId);
  if (!seg) return null;
  return {
    resolvedFile,
    prompt: seg[field] ?? "",
    duration: seg.duration_seconds ?? 4,
  };
}


export function StudioCanvasRouter() {
  const { t } = useTranslation("dashboard");
  const {
    currentProjectData,
    currentProjectName,
    currentScripts,
    activeEpisodeScript,
    projectDetailLoading,
  } = useProjectsStore();
  const demoMode = useDemoWorkbench();

  const capabilitiesEnabled = !demoMode && !isDemoProject(currentProjectName);

  const capabilities = useModelCapabilities({
    projectName: currentProjectName,
    enabled: capabilitiesEnabled,
  });

  const generatingCharacterNames = useActiveResourceIds("character", currentProjectName);
  const generatingSceneNames = useActiveResourceIds("scene", currentProjectName);
  const generatingPropNames = useActiveResourceIds("prop", currentProjectName);
  const generatingProductNames = useActiveResourceIds("product", currentProjectName);

  const refreshProject = useCallback(
    (invalidateKeys: string[] = []): Promise<boolean> =>
      currentProjectName
        ? useProjectsStore
            .getState()
            .refreshProject(currentProjectName, {
              invalidateKeys,
              scope: activeEpisodeScript.episode ? { episodes: [activeEpisodeScript.episode] } : undefined,
            })
            .then((result) => result === "success")
        : Promise.resolve(false),
    [currentProjectName, activeEpisodeScript.episode],
  );

  const refreshAfterAction = useCallback(
    async (): Promise<boolean> =>
      currentProjectName ? (await refreshAfterWrite(currentProjectName, t)) === "success" : false,
    [currentProjectName, t],
  );

  const saves = useRef(new Map<string, number>());
  const handleUpdatePrompt = useCallback(async (
    segmentId: string,
    patch: Record<string, unknown>,
    scriptFile?: string,
  ): Promise<boolean> => {
    if (!currentProjectName) throw new Error(t("common:no_project_selected"));
    const pending = saves.current;
    const target = JSON.stringify([currentProjectName, scriptFile]);
    pending.set(target, (pending.get(target) ?? 0) + 1);
    try {
      const mode = currentProjectData?.content_mode ?? "narration";
      if (mode === "ad") {
        await API.updateShot(currentProjectName, segmentId, scriptFile ?? "", patch);
      } else if (mode === "drama") {
        await API.updateScene(currentProjectName, segmentId, scriptFile ?? "", patch);
      } else {
        await API.updateSegment(currentProjectName, segmentId, { script_file: scriptFile, ...patch });
      }
      return await refreshProject();
    } finally {
      const remaining = (pending.get(target) ?? 1) - 1;
      if (remaining) pending.set(target, remaining);
      else pending.delete(target);
    }
  }, [currentProjectName, currentProjectData, refreshProject, t]);

  const handleMoveShot = useCallback(async (
    shotId: string,
    afterId: string | null,
    scriptFile?: string,
  ): Promise<boolean> => {
    if (!currentProjectName || !currentScripts) return false;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return false;
    try {
      await API.moveScriptItem(currentProjectName, resolvedFile, shotId, afterId);
      return await refreshAfterAction();
    } catch (err) {
      useAppStore.getState().pushToast(t("reorder_shot_failed", { message: errMsg(err) }), "error");
      return false;
    }
  }, [currentProjectName, currentScripts, refreshAfterAction, t]);

  const refreshAfterStructureEdit = useCallback(async () => {
    if (!(await refreshProject())) {
      useAppStore.getState().pushToast(t("shot_structure_refresh_failed"), "warning");
    }
  }, [refreshProject, t]);

  const handleInsertShot = useCallback(async (
    afterId: string | null,
    novelText: string | undefined,
    scriptFile?: string,
  ): Promise<boolean> => {
    if (!currentProjectName || !currentScripts) return false;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return false;
    try {
      await API.insertScriptItem(currentProjectName, resolvedFile, { afterId: afterId ?? undefined, novelText });
    } catch (err) {
      useAppStore.getState().pushToast(t("shot_insert_failed", { message: errMsg(err) }), "error");
      return false;
    }
    await refreshAfterStructureEdit();
    return true;
  }, [currentProjectName, currentScripts, refreshAfterStructureEdit, t]);

  const handleRemoveShot = useCallback(async (itemId: string, scriptFile?: string): Promise<boolean> => {
    if (!currentProjectName || !currentScripts) return false;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return false;
    try {
      await API.removeScriptItem(currentProjectName, itemId, resolvedFile);
    } catch (err) {
      useAppStore.getState().pushToast(t("shot_remove_failed", { message: errMsg(err) }), "error");
      return false;
    }
    await refreshAfterStructureEdit();
    return true;
  }, [currentProjectName, currentScripts, refreshAfterStructureEdit, t]);

  const handleUpdateEpisodeTitle = useCallback(async (episode: number, title: string) => {
    if (!currentProjectName) return;
    try {
      await API.updateEpisode(currentProjectName, episode, { title });
    } catch (err) {
      useAppStore.getState().pushToast(t("episode_title_update_failed", { message: errMsg(err) }), "error");
      throw err;
    }
    await refreshAfterAction();
  }, [currentProjectName, refreshAfterAction, t]);

  const handleGenerateStoryboard = useCallback(async (episode: number, segmentId: string, scriptFile?: string) => {
    if (!currentProjectName) return;
    const resolved = resolveSegmentPrompt(currentProjectName, episode, saves.current, segmentId, "image_prompt", scriptFile);
    if (!resolved) return;
    try {
      await enqueueStoryboard(
        currentProjectName,
        segmentId,
        resolved.prompt as string | Record<string, unknown>,
        resolved.resolvedFile,
      );
    } catch (err) {
      useAppStore.getState().pushToast(t("generate_storyboard_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const handleGenerateVideo = useCallback(async (episode: number, segmentId: string, scriptFile?: string) => {
    if (!currentProjectName) return;
    const resolved = resolveSegmentPrompt(currentProjectName, episode, saves.current, segmentId, "video_prompt", scriptFile);
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
      useAppStore.getState().pushToast(t("generate_video_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const ensureAudioProviderConfigured = useCallback((): boolean => {
    const cfg = useConfigStatusStore.getState();
    if (cfg.initialized && !cfg.hasMediaType("audio")) {
      useAppStore.getState().pushToast(t("audio_provider_not_configured_toast"), "error");
      return false;
    }
    return true;
  }, [t]);

  const handleGenerateNarration = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    if (!ensureAudioProviderConfigured()) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    try {
      await enqueueNarration(currentProjectName, segmentId, resolvedFile);
    } catch (err) {
      useAppStore.getState().pushToast(t("generate_narration_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentScripts, ensureAudioProviderConfigured, t]);

  const handleGenerateEpisodeNarration = useCallback(async (scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    if (!ensureAudioProviderConfigured()) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    try {
      await enqueueEpisodeNarration(currentProjectName, resolvedFile);
    } catch (err) {
      useAppStore.getState().pushToast(t("generate_narration_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, currentScripts, ensureAudioProviderConfigured, t]);

  const narrationGenerationEnabled = currentProjectData?.narration_delivery === "use_tts";

  const handleViewWorkflowUnit = useCallback((unitId: string) => {
    useAppStore.getState().triggerScrollTo({
      type: normalizeRoute(currentProjectData?.generation_mode) === "reference_video"
        ? "reference_unit"
        : "segment",
      id: unitId,
    });
  }, [currentProjectData?.generation_mode]);

  const handleWorkflowRegenerate = useCallback(async (
    episode: number,
    stepId: string,
    unitIds: string[],
    scriptFile: string,
  ) => {
    if (!currentProjectName || !currentScripts) return;
    for (const unitId of unitIds) {
      try {
        if (stepId === "storyboard") {
          // react-doctor-disable-next-line react-doctor/async-await-in-loop -- 逐个入队保持任务队列与画布单元同序，并发提交会打乱入队顺序
          await handleGenerateStoryboard(episode, unitId, scriptFile);
        } else if (stepId === "video") {
          await handleGenerateVideo(episode, unitId, scriptFile);
        }
      } catch (err) {
        useAppStore.getState().pushToast(t("generate_video_failed", { message: errMsg(err) }), "error");
      }
    }
  }, [
    currentProjectName,
    currentScripts,
    handleGenerateStoryboard,
    handleGenerateVideo,
    t,
  ]);

  const handleGenerateCharacter = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueCharacter(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(t("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const handleGenerateScene = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueScene(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(t("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const handleGenerateProp = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueProp(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(t("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const handleGenerateProduct = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueProduct(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(t("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const handleGenerateGrid = useCallback(async (episode: number, scriptFile: string, sceneIds?: string[]) => {
    if (!currentProjectName) return;
    try {
      await enqueueGrid(currentProjectName, episode, scriptFile, sceneIds);
    } catch (err) {
      useAppStore.getState().pushToast(t("grid_generation_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName, t]);

  const handleRestoreAsset = refreshAfterAction;

  const handleGenerateCharacterVoid = useCallback((...args: Parameters<typeof handleGenerateCharacter>) => {
    void handleGenerateCharacter(...args).catch(console.error);
  }, [handleGenerateCharacter]);
  const handleGenerateSceneVoid = useCallback((...args: Parameters<typeof handleGenerateScene>) => {
    void handleGenerateScene(...args).catch(console.error);
  }, [handleGenerateScene]);
  const handleGeneratePropVoid = useCallback((...args: Parameters<typeof handleGenerateProp>) => {
    void handleGenerateProp(...args).catch(console.error);
  }, [handleGenerateProp]);
  const handleGenerateProductVoid = useCallback((...args: Parameters<typeof handleGenerateProduct>) => {
    void handleGenerateProduct(...args).catch(console.error);
  }, [handleGenerateProduct]);

  if (!currentProjectName || projectDetailLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
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
          readOnly={demoMode}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_EPISODES}`}>
        {demoMode || currentProjectData?.content_mode === "ad" ? (
          <Redirect to="/" />
        ) : (
          <EpisodesView key={currentProjectName} projectName={currentProjectName} />
        )}
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_CHARACTERS}`}>
        <CharactersPage
          key={currentProjectName}
          projectName={currentProjectName}
          characters={currentProjectData?.characters ?? {}}
          readOnly={demoMode}
          onGenerateCharacter={handleGenerateCharacterVoid}
          onRestoreCharacterVersion={handleRestoreAsset}
          onRefreshProject={refreshAfterAction}
          generatingCharacterNames={generatingCharacterNames}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_SCENES}`}>
        <ScenesPage
          key={currentProjectName}
          projectName={currentProjectName}
          scenes={currentProjectData?.scenes ?? {}}
          readOnly={demoMode}
          onGenerateScene={handleGenerateSceneVoid}
          onRestoreSceneVersion={handleRestoreAsset}
          onRefreshProject={refreshAfterAction}
          generatingSceneNames={generatingSceneNames}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_PROPS}`}>
        <PropsPage
          key={currentProjectName}
          projectName={currentProjectName}
          props={currentProjectData?.props ?? {}}
          readOnly={demoMode}
          onGenerateProp={handleGeneratePropVoid}
          onRestorePropVersion={handleRestoreAsset}
          onRefreshProject={refreshAfterAction}
          generatingPropNames={generatingPropNames}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_PRODUCTS}`}>
        <ProductsPage
          key={currentProjectName}
          projectName={currentProjectName}
          products={currentProjectData?.products ?? {}}
          readOnly={demoMode}
          onGenerateProduct={handleGenerateProductVoid}
          onRestoreProductVersion={handleRestoreAsset}
          onRefreshProject={refreshAfterAction}
          generatingProductNames={generatingProductNames}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_FILES}`}>
        {demoMode ? <Redirect to="/" /> : <FileView key={currentProjectName} projectName={currentProjectName} />}
      </Route>

      <Route path={EPISODE_ROUTE_PATH}>
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find((e) => e.episode === epNum);
          const scriptFile = episode?.script_file ? stripScriptsPrefix(episode.script_file) : undefined;
          const script = scriptFile ? (currentScripts[scriptFile] ?? null) : null;
          const scriptLoading =
            !demoMode &&
            !script &&
            Boolean(scriptFile) &&
            episode?.script_status !== "none" &&
            episode?.script_status !== "segmented" &&
            activeEpisodeScript.projectName === currentProjectName &&
            ((activeEpisodeScript.episode === epNum && activeEpisodeScript.status === "loading") ||
              (episode?.script_status === "generated" && activeEpisodeScript.episode !== epNum));
          const route = normalizeRoute(currentProjectData?.generation_mode);
          const durationOptions = capabilities.supportedDurations ?? undefined;
          const planDurationOptions = capabilities.planningDurations ?? undefined;
          const durationWarningReason = (seconds: number) =>
            durationOutOfRangeReason(seconds, capabilities);
          const durationEndpointFixed = capabilities.durationEndpointFixed;
          const hasDraft =
            episode?.script_status === "segmented" || episode?.script_status === "generated";
          const isAd = currentProjectData?.content_mode === "ad";
          return (
            <EpisodePage
              scriptLoading={scriptLoading}
              projectName={currentProjectName}
              episode={epNum}
              projectData={currentProjectData}
              script={script}
              demo={demoMode}
              onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
              onViewUnit={handleViewWorkflowUnit}
              onRegenerate={
                route === "reference_video" || !scriptFile
                  ? undefined
                  : (stepId, unitIds) => void handleWorkflowRegenerate(epNum, stepId, unitIds, scriptFile)
              }
              renderCanvas={({ view, onViewChange }) => (
                <LazyBoundary variant="pane">
                  {route === "reference_video" ? (
                    <ReferenceVideoCanvas
                      key={`${currentProjectName}::${epNum}`}
                      projectName={currentProjectName}
                      episode={epNum}
                      view={view}
                      onViewChange={onViewChange}
                      hasScript={Boolean(script)}
                      showPreprocess={!isAd}
                      freeDuration={isAd}
                      videoModelUnresolved={capabilities.videoModelUnresolved}
                      planDurationOptions={planDurationOptions}
                    />
                  ) : gridStoryboardEnabled(currentProjectData) ? (
                    <GridImageToVideoCanvas
                      key={`${currentProjectName}::${epNum}`}
                      projectName={currentProjectName}
                      episode={epNum}
                      view={view}
                      onViewChange={onViewChange}
                      hasDraft={hasDraft}
                      episodeScript={script}
                      scriptFile={scriptFile ?? undefined}
                      projectData={currentProjectData}
                      durationOptions={durationOptions}
                      planDurationOptions={planDurationOptions}
                      durationWarningReason={durationWarningReason}
                      durationEndpointFixed={durationEndpointFixed}
                      videoModelUnresolved={capabilities.videoModelUnresolved}
                      lastFrame={capabilities.lastFrame}
                      capabilitiesLoading={capabilities.loading}
                      onUpdatePrompt={handleUpdatePrompt}
                      onGenerateStoryboard={(id) => void handleGenerateStoryboard(epNum, id, scriptFile)}
                      onGenerateVideo={(id) => handleGenerateVideo(epNum, id, scriptFile)}
                      onGenerateNarration={narrationGenerationEnabled ? voidPromise(handleGenerateNarration) : undefined}
                      onGenerateEpisodeNarration={narrationGenerationEnabled ? voidPromise(handleGenerateEpisodeNarration) : undefined}
                      onGenerateGrid={handleGenerateGrid}
                      onRestoreStoryboard={handleRestoreAsset}
                      onRestoreVideo={handleRestoreAsset}
                      onMoveShot={handleMoveShot}
                      onInsertShot={handleInsertShot}
                      onRemoveShot={handleRemoveShot}
                    />
                  ) : (
                    <TimelineCanvas
                      key={`${currentProjectName}::${epNum}`}
                      projectName={currentProjectName}
                      episode={epNum}
                      view={view}
                      onViewChange={onViewChange}
                      hasDraft={hasDraft}
                      episodeScript={script}
                      scriptFile={scriptFile ?? undefined}
                      projectData={currentProjectData}
                      durationOptions={durationOptions}
                      planDurationOptions={planDurationOptions}
                      durationWarningReason={durationWarningReason}
                      durationEndpointFixed={durationEndpointFixed}
                      videoModelUnresolved={capabilities.videoModelUnresolved}
                      lastFrame={capabilities.lastFrame}
                      capabilitiesLoading={capabilities.loading}
                      onUpdatePrompt={handleUpdatePrompt}
                      onMoveShot={handleMoveShot}
                      onInsertShot={handleInsertShot}
                      onRemoveShot={handleRemoveShot}
                      onGenerateStoryboard={(id) => void handleGenerateStoryboard(epNum, id, scriptFile)}
                      onGenerateVideo={(id) => handleGenerateVideo(epNum, id, scriptFile)}
                      onGenerateNarration={narrationGenerationEnabled ? voidPromise(handleGenerateNarration) : undefined}
                      onGenerateEpisodeNarration={narrationGenerationEnabled ? voidPromise(handleGenerateEpisodeNarration) : undefined}
                      onRestoreStoryboard={handleRestoreAsset}
                      onRestoreVideo={handleRestoreAsset}
                    />
                  )}
                </LazyBoundary>
              )}
            />
          );
        }}
      </Route>

      <Route>
        <WorkspaceNotFound />
      </Route>
    </Switch>
  );
}
