import { useCallback } from "react";
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
import { useProjectsStore } from "@/stores/projects-store";
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
import type { EpisodeScript } from "@/types/script";

// Reference-to-video/grid canvases serve corresponding project generation modes, loaded on demand; common shot-by-shot canvas
// (TimelineCanvas), asset, and episode pages load with the workspace.
const ReferenceVideoCanvas = lazyNamed(() => import("./reference/ReferenceVideoCanvas"), "ReferenceVideoCanvas");
const GridImageToVideoCanvas = lazyNamed(() => import("./grid/GridImageToVideoCanvas"), "GridImageToVideoCanvas");

/** Episode route path shared with its rendering `<Route>`. */
const EPISODE_ROUTE_PATH = `/${WORKSPACE_ROUTE_EPISODES}/:episodeId`;

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
  if ("video_units" in script) return null;
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
  const { currentProjectData, currentProjectName, currentScripts, projectDetailLoading } =
    useProjectsStore();
  // Demos pass readOnly through asset canvases; workbench timeline components read the same criterion directly.
  // useDemoWorkbench() unifies routing/store lag decisions; consume that sole source here.
  const demoMode = useDemoWorkbench();

  // Demos send no real capability requests. Switching demo→real clears demoMode before stores update;
  // separately check currentProjectName to cover the frame still holding old demo names.
  const capabilitiesEnabled = !demoMode && !isDemoProject(currentProjectName);

  // Shot-duration editors use candidates narrowed by linked constraints, preventing combinations inevitably rejected after enqueue;
  // leave saved out-of-range values unchanged; ShotDetail explains causes and guides reselection.
  // Omit candidate models/resolutions/reference paths: servers resolve actual project-mode i2v/r2v buckets
  // and their saved tiers. Passing project-default `video_backend` overrides specialized buckets and diverges from execution.
  // Capabilities follow project generation modes consistently project-wide, without episode IDs.
  const capabilities = useModelCapabilities({
    projectName: currentProjectName,
    enabled: capabilitiesEnabled,
  });

  // Derive loading from queues instead of local state; active/latest-row invariants live in store selectors.
  const generatingCharacterNames = useActiveResourceIds("character", currentProjectName);
  const generatingSceneNames = useActiveResourceIds("scene", currentProjectName);
  const generatingPropNames = useActiveResourceIds("prop", currentProjectName);
  const generatingProductNames = useActiveResourceIds("product", currentProjectName);

  // Refresh project data; return local-store synchronization success so callers advance order-dependent UI only when appropriate.
  // projects-store refreshProject consolidates in-flight deduplication/preserve-on-failure; express intent only here.
  // "cancelled" (project-switch cancellation scopes, etc.) and "failed" both mean unsynchronized, returning false
  // without notices here. Callers with dedicated feedback (PartialSaveError for shot saves/structural editing) handle it;
  // other post-write refreshes use refreshAfterAction below.
  const refreshProject = useCallback(
    (invalidateKeys: string[] = []): Promise<boolean> =>
      currentProjectName
        ? useProjectsStore
            .getState()
            .refreshProject(currentProjectName, { invalidateKeys })
            .then((result) => result === "success")
        : Promise.resolve(false),
    [currentProjectName],
  );

  // Successful-write refresh reports failures (refreshAfterWrite), resolving local synchronization success so
  // callers decide whether to advance new-data-dependent actions such as selection or success reporting.
  const refreshAfterAction = useCallback(
    async (): Promise<boolean> =>
      currentProjectName ? (await refreshAfterWrite(currentProjectName, t)) === "success" : false,
    [currentProjectName, t],
  );

  // ---- Timeline action callbacks ----
  // These receive scriptFile from TimelineCanvas so they always use the active episode's script.
  // Shot editing-unit save propagates PATCH failures faithfully for the unsaved notice bar.
  // Resolve whether local stores synchronized new scripts; persisted PATCH with failed/canceled refresh returns false.
  // Stores still hold old prompts then, so callers must not generate/enqueue from stale scripts.
  const handleUpdatePrompt = useCallback(async (
    segmentId: string,
    patch: Record<string, unknown>,
    scriptFile?: string,
  ): Promise<boolean> => {
    if (!currentProjectName) throw new Error(t("common:no_project_selected"));
    const mode = currentProjectData?.content_mode ?? "narration";
    if (mode === "ad") {
      await API.updateShot(currentProjectName, segmentId, scriptFile ?? "", patch);
    } else if (mode === "drama") {
      await API.updateScene(currentProjectName, segmentId, scriptFile ?? "", patch);
    } else {
      await API.updateSegment(currentProjectName, segmentId, { script_file: scriptFile, ...patch });
    }
    return refreshProject();
  }, [currentProjectName, currentProjectData, refreshProject, t]);

  // Reorder across script shapes: move after afterId, null to front.
  // Return success so editors follow shots to new positions.
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
      // Report success only after stores hold new order: failed refresh retains old segments,
      // so moving selection to new positions would silently select other shots.
      return await refreshAfterAction();
    } catch (err) {
      useAppStore.getState().pushToast(t("reorder_shot_failed", { message: errMsg(err) }), "error");
      return false;
    }
  }, [currentProjectName, currentScripts, refreshAfterAction, t]);

  // Timeline add/remove uses server current script revisions without snapshots here.
  // Report success once writes commit; failed local refresh only requests reload, without retryable state,
  // preventing duplicate additions or repeated removal of already-removed shots.
  const refreshAfterStructureEdit = useCallback(async () => {
    if (!(await refreshProject())) {
      useAppStore.getState().pushToast(t("shot_structure_refresh_failed"), "warning");
    }
  }, [refreshProject, t]);

  // Null afterId appends (first entry in empty scripts).
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
      throw err; // Preserve EditableEpisodeTitle editing without accidental clearing.
    }
    // Title already saved: refresh failures only notify, avoiding editing states encouraging duplicate submissions.
    await refreshAfterAction();
  }, [currentProjectName, refreshAfterAction, t]);

  // Generation callbacks read current store scripts at call time, not render closures. Save-and-generate saves/refetches
  // within one click, whose callback still belongs to pre-click rendering with old prompts in its closure.
  const handleGenerateStoryboard = useCallback(async (segmentId: string, scriptFile?: string) => {
    const scripts = useProjectsStore.getState().currentScripts;
    if (!currentProjectName || !scripts) return;
    const resolved = resolveSegmentPrompt(scripts, segmentId, "image_prompt", scriptFile);
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

  const handleGenerateVideo = useCallback(async (segmentId: string, scriptFile?: string) => {
    const scripts = useProjectsStore.getState().currentScripts;
    if (!currentProjectName || !scripts) return;
    const resolved = resolveSegmentPrompt(scripts, segmentId, "video_prompt", scriptFile);
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

  // Explain missing audio providers upfront; backend preenqueue 400 checks enforce the same meaning.
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

  // Post-production projects do not generate narration audio: hide generation entries while keeping existing audio previews.
  const narrationGenerationEnabled = currentProjectData?.narration_delivery === "use_tts";

  // ---- Workflow panel callbacks ----
  // Panels state facts only, delegating actions to existing entries: use the Agent-location scrollTarget seam for navigation
  // and existing enqueue callbacks for regeneration, without panel-owned players/enqueue paths.
  const handleViewWorkflowUnit = useCallback((unitId: string) => {
    useAppStore.getState().triggerScrollTo({
      type: normalizeRoute(currentProjectData?.generation_mode) === "reference_video"
        ? "reference_unit"
        : "segment",
      id: unitId,
    });
  }, [currentProjectData?.generation_mode]);

  // Callers supply the current episode's script file; multiepisode currentScripts contains all episodes,
  // so taking its first key regenerates another episode's script and users see the wrong episode redone.
  const handleWorkflowRegenerate = useCallback(async (
    stepId: string,
    unitIds: string[],
    scriptFile: string,
  ) => {
    if (!currentProjectName || !currentScripts) return;
    for (const unitId of unitIds) {
      try {
        if (stepId === "storyboard") {
          // react-doctor-disable-next-line react-doctor/async-await-in-loop -- Sequential enqueue preserves queue/canvas-unit order; concurrent submission would reorder it
          await handleGenerateStoryboard(unitId, scriptFile);
        } else if (stepId === "video") {
          await handleGenerateVideo(unitId, scriptFile);
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

  // ---- Asset sheet generation callbacks ----
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

  // Refresh after version restoration/media uploads; synchronization success controls caller success reporting.
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

  // `currentProjectName` lands before details (router.tsx initial-load comments);
  // checking it alone renders interactive empty-project pages for deep links (/characters etc.) or slow details.
  // `projectDetailLoading` signals whether details actually arrived.
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
        {/* Demos are absent server-side; ads/short films are single-episode and bypass episode management. Sidebar hides entries; guard direct URLs too. */}
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
        {/* Demos are absent server-side; sidebar hides entries; guard direct URLs too. */}
        {demoMode ? <Redirect to="/" /> : <FileView key={currentProjectName} projectName={currentProjectName} />}
      </Route>

      <Route path={EPISODE_ROUTE_PATH}>
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find((e) => e.episode === epNum);
          const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
          const script = scriptFile ? (currentScripts[scriptFile] ?? null) : null;
          const route = normalizeRoute(currentProjectData?.generation_mode);
          // Server narrows by project generation mode/reference-image path and saved resolution.
          // reference_video reference constraints apply per unit, not episode; servers choose each unit's bucket/tiers
          // from available references, arriving with unit lists via reference-video-store, not supplied here.
          const durationOptions = capabilities.supportedDurations ?? undefined;
          // Content confirmation uses script-plan duration tiers: fixed endpoints have empty supportedDurations but planning still borrows tiers.
          const planDurationOptions = capabilities.planningDurations ?? undefined;
          const durationWarningReason = (seconds: number) =>
            durationOutOfRangeReason(seconds, capabilities);
          // Empty-tier causes need distinct explanations: model durations unregistered versus workflow-controlled length.
          const durationEndpointFixed = capabilities.durationEndpointFixed;
          const hasDraft =
            episode?.script_status === "segmented" || episode?.script_status === "generated";
          const isAd = currentProjectData?.content_mode === "ad";
          return (
            <EpisodePage
              projectName={currentProjectName}
              episode={epNum}
              projectData={currentProjectData}
              script={script}
              demo={demoMode}
              onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
              onViewUnit={handleViewWorkflowUnit}
              // ReferenceVideoCanvas handles reference-to-video enqueue via its own whole-batch admission;
              // these per-unit callbacks cannot resolve video_units prompts and would do nothing.
              // This mode exposes only view navigation, with regeneration on destination unit cards.
              onRegenerate={
                route === "reference_video" || !scriptFile
                  ? undefined
                  : (stepId, unitIds) => void handleWorkflowRegenerate(stepId, unitIds, scriptFile)
              }
              renderCanvas={({ view, onViewChange }) => (
                // Lazy chunks pending/failed show canvas-only placeholders, retaining visible headers/view switches.
                <LazyBoundary variant="pane">
                  {route === "reference_video" ? (
                    <ReferenceVideoCanvas
                      // Reusing epNum across projects without remount leaves optimisticUnitIds / prevTaskStatusRef
                      // from previous projects (e.g. persistent "E1U1" in sets). New-project same-name units
                      // would falsely match "optimistic && !hasQueueRow" busy. Key by project::episode
                      // to isolate naturally per project without explicit pruning.
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
                      onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
                      onGenerateVideo={handleGenerateVideo}
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
                      // Like ReferenceVideoCanvas above, same epNum across projects without remount
                      // retains TimelineCanvas useState / useRef values (selected scene, draft buffers,
                      // scroll positions, etc.). Include projectName in keys for natural isolation.
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
                      onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
                      onGenerateVideo={handleGenerateVideo}
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

      {/* Unrouted child paths, including retired lorebook/clues: empty state inside canvas, retaining the shell. */}
      <Route>
        <WorkspaceNotFound />
      </Route>
    </Switch>
  );
}
