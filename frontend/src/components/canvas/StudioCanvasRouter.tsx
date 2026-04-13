import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { Route, Switch, Redirect, useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { LorebookGallery } from "./lorebook/LorebookGallery";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./OverviewCanvas";
import { SourceFileViewer } from "./SourceFileViewer";
import { AddCharacterForm } from "./lorebook/AddCharacterForm";
import { AddClueForm } from "./lorebook/AddClueForm";
import { API } from "@/api";
import { buildEntityRevisionKey } from "@/utils/project-changes";
import { getProviderModels, getCustomProviderModels, lookupSupportedDurations } from "@/utils/provider-models";
import type { Clue, CustomProviderInfo, ProviderInfo } from "@/types";
import type { EpisodeScript } from "@/types/script";

// ---------------------------------------------------------------------------
// resolveSegmentPrompt — shared segment lookup for generate storyboard/video
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
      : script.scenes.find((s) => s.scene_id === segmentId);
  return {
    resolvedFile,
    prompt: seg?.[field] ?? "",
    duration: seg?.duration_seconds ?? 4,
  };
}

// ---------------------------------------------------------------------------
// StudioCanvasRouter — reads Zustand store data and renders the correct
// canvas view based on the nested route within /app/projects/:projectName.
// ---------------------------------------------------------------------------

export function StudioCanvasRouter() {
  const { t } = useTranslation("dashboard");
  const tRef = useRef(t);
  tRef.current = t;
  const { currentProjectData, currentProjectName, currentScripts } =
    useProjectsStore();

  const [addingCharacter, setAddingCharacter] = useState(false);
  const [addingClue, setAddingClue] = useState(false);

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [globalVideoBackend, setGlobalVideoBackend] = useState("");

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

  const durationOptions = useMemo(() => {
    const backend = currentProjectData?.video_backend || globalVideoBackend;
    if (!backend) return undefined;
    return lookupSupportedDurations(providers, backend, customProviders);
  }, [providers, customProviders, globalVideoBackend, currentProjectData?.video_backend]);

  // 从任务队列派生 loading 状态（替代本地 state）
  const tasks = useTasksStore((s) => s.tasks);
  const generatingCharacterNames = useMemo(() => {
    const names = new Set<string>();
    for (const t of tasks) {
      if (
        t.task_type === "character" &&
        t.project_name === currentProjectName &&
        (t.status === "queued" || t.status === "running")
      ) {
        names.add(t.resource_id);
      }
    }
    return names;
  }, [tasks, currentProjectName]);
  const generatingClueNames = useMemo(() => {
    const names = new Set<string>();
    for (const t of tasks) {
      if (
        t.task_type === "clue" &&
        t.project_name === currentProjectName &&
        (t.status === "queued" || t.status === "running")
      ) {
        names.add(t.resource_id);
      }
    }
    return names;
  }, [tasks, currentProjectName]);

  // 刷新项目数据
  const refreshProject = useCallback(async (invalidateKeys: string[] = []) => {
    if (!currentProjectName) return;
    try {
      const res = await API.getProject(currentProjectName);
      useProjectsStore.getState().setCurrentProject(
        currentProjectName,
        res.project,
        res.scripts ?? {},
        res.asset_fingerprints,
      );
      if (invalidateKeys.length > 0) {
        useAppStore.getState().invalidateEntities(invalidateKeys);
      }
    } catch {
      // 静默失败
    }
  }, [currentProjectName]);

  // ---- Timeline action callbacks ----
  // These receive scriptFile from TimelineCanvas so they always use the active episode's script.
  const handleUpdatePrompt = useCallback(async (segmentId: string, field: string, value: unknown, scriptFile?: string) => {
    if (!currentProjectName) return;
    const mode = currentProjectData?.content_mode ?? "narration";
    try {
      if (mode === "drama") {
        await API.updateScene(currentProjectName, segmentId, scriptFile ?? "", { [field]: value });
      } else {
        await API.updateSegment(currentProjectName, segmentId, { script_file: scriptFile, [field]: value });
      }
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_prompt_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, currentProjectData, refreshProject]);

  const handleGenerateStoryboard = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolved = resolveSegmentPrompt(currentScripts, segmentId, "image_prompt", scriptFile);
    if (!resolved) return;
    try {
      await API.generateStoryboard(
        currentProjectName,
        segmentId,
        resolved.prompt as string | Record<string, unknown>,
        resolved.resolvedFile,
      );
      useAppStore.getState().pushToast(tRef.current("storyboard_task_submitted_toast", { id: segmentId }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("generate_storyboard_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, currentScripts]);

  const handleGenerateVideo = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolved = resolveSegmentPrompt(currentScripts, segmentId, "video_prompt", scriptFile);
    if (!resolved) return;
    try {
      await API.generateVideo(
        currentProjectName,
        segmentId,
        resolved.prompt as string | Record<string, unknown>,
        resolved.resolvedFile,
        resolved.duration,
      );
      useAppStore.getState().pushToast(tRef.current("video_task_submitted_toast", { id: segmentId }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("generate_video_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, currentScripts]);

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
      useAppStore.getState().pushToast(tRef.current("update_character_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateCharacter = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await API.generateCharacter(
        currentProjectName,
        name,
        currentProjectData?.characters?.[name]?.description ?? "",
      );
      useAppStore
        .getState()
        .pushToast(tRef.current("character_task_submitted_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: (err as Error).message }), "error");
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
      setAddingCharacter(false);
      useAppStore.getState().pushToast(tRef.current("character_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  // ---- Clue CRUD callbacks ----
  const handleUpdateClue = useCallback(async (name: string, updates: Partial<Clue>) => {
    if (!currentProjectName) return;
    try {
      await API.updateClue(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_clue_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateClue = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await API.generateClue(
        currentProjectName,
        name,
        currentProjectData?.clues?.[name]?.description ?? "",
      );
      useAppStore
        .getState()
        .pushToast(tRef.current("clue_task_submitted_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddClueSubmit = useCallback(async (name: string, clueType: string, description: string, importance: string) => {
    if (!currentProjectName) return;
    try {
      await API.addClue(currentProjectName, name, clueType, description, importance);
      await refreshProject();
      setAddingClue(false);
      useAppStore.getState().pushToast(tRef.current("clue_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateGrid = useCallback(async (episode: number, scriptFile: string, sceneIds?: string[]) => {
    if (!currentProjectName) return;
    try {
      const result = await API.generateGrid(currentProjectName, episode, scriptFile, sceneIds);
      useAppStore.getState().pushToast(result.message, "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("grid_generation_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName]);

  const handleRestoreAsset = useCallback(async () => {
    await refreshProject();
  }, [refreshProject]);

  const [location] = useLocation();

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

      {/* Characters & Clues share one LorebookGallery to avoid remount flash */}
      {(location === "/characters" || location === "/clues") && (
        <div className="p-4">
          <LorebookGallery
            projectName={currentProjectName}
            characters={currentProjectData?.characters ?? {}}
            clues={currentProjectData?.clues ?? {}}
            mode={location === "/clues" ? "clues" : "characters"}
            onSaveCharacter={handleSaveCharacter}
            onUpdateClue={handleUpdateClue}
            onGenerateCharacter={handleGenerateCharacter}
            onGenerateClue={handleGenerateClue}
            onRestoreCharacterVersion={handleRestoreAsset}
            onRestoreClueVersion={handleRestoreAsset}
            generatingCharacterNames={generatingCharacterNames}
            generatingClueNames={generatingClueNames}
            onAddCharacter={() => setAddingCharacter(true)}
            onAddClue={() => setAddingClue(true)}
          />
          {addingCharacter && (
            <AddCharacterForm
              onSubmit={handleAddCharacterSubmit}
              onCancel={() => setAddingCharacter(false)}
            />
          )}
          {addingClue && (
            <AddClueForm
              onSubmit={handleAddClueSubmit}
              onCancel={() => setAddingClue(false)}
            />
          )}
        </div>
      )}

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
          const episode = currentProjectData?.episodes?.find(
            (e) => e.episode === epNum,
          );
          const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
          const script = scriptFile
            ? (currentScripts[scriptFile] ?? null)
            : null;

          const hasDraft = episode?.script_status === "segmented" || episode?.script_status === "generated";

          return (
            <TimelineCanvas
              key={epNum}
              projectName={currentProjectName}
              episode={epNum}
              episodeTitle={episode?.title}
              hasDraft={hasDraft}
              episodeScript={script}
              scriptFile={scriptFile ?? undefined}
              projectData={currentProjectData}
              durationOptions={durationOptions}
              onUpdatePrompt={handleUpdatePrompt}
              onGenerateStoryboard={handleGenerateStoryboard}
              onGenerateVideo={handleGenerateVideo}
              onGenerateGrid={handleGenerateGrid}
              onRestoreStoryboard={handleRestoreAsset}
              onRestoreVideo={handleRestoreAsset}
            />
          );
        }}
      </Route>
    </Switch>
  );
}
