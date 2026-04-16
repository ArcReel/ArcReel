import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { voidPromise } from "@/utils/async";
import { Route, Switch, Redirect } from "wouter";
import { useTranslation } from "react-i18next";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { CharacterCard } from "./lorebook/CharacterCard";
import { SceneCard } from "./lorebook/SceneCard";
import { PropCard } from "./lorebook/PropCard";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./OverviewCanvas";
import { SourceFileViewer } from "./SourceFileViewer";
import { AddCharacterForm } from "./lorebook/AddCharacterForm";
import { API } from "@/api";
import { buildEntityRevisionKey } from "@/utils/project-changes";
import { getProviderModels, getCustomProviderModels, lookupSupportedDurations } from "@/utils/provider-models";
import type { Scene, Prop, CustomProviderInfo, ProviderInfo } from "@/types";
import type { EpisodeScript } from "@/types/script";

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

  const [addingCharacter, setAddingCharacter] = useState(false);
  const [addingScene, setAddingScene] = useState(false);
  const [addingProp, setAddingProp] = useState(false);

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
  const generatingSceneNames = useMemo(() => {
    const names = new Set<string>();
    for (const t of tasks) {
      if (
        t.task_type === "scene" &&
        t.project_name === currentProjectName &&
        (t.status === "queued" || t.status === "running")
      ) {
        names.add(t.resource_id);
      }
    }
    return names;
  }, [tasks, currentProjectName]);
  const generatingPropNames = useMemo(() => {
    const names = new Set<string>();
    for (const t of tasks) {
      if (
        t.task_type === "prop" &&
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

  // ---- Scene CRUD callbacks ----
  const handleUpdateScene = useCallback(async (name: string, updates: Partial<Scene>) => {
    if (!currentProjectName) return;
    try {
      await API.updateProjectScene(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_scene_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateScene = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await API.generateProjectScene(currentProjectName, name, currentProjectData?.scenes?.[name]?.description ?? "");
      useAppStore.getState().pushToast(tRef.current("scene_task_submitted_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddSceneSubmit = useCallback(async (name: string, description: string) => {
    if (!currentProjectName) return;
    try {
      await API.addProjectScene(currentProjectName, name, description);
      await refreshProject();
      setAddingScene(false);
      useAppStore.getState().pushToast(tRef.current("scene_added_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("add_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  // ---- Prop CRUD callbacks ----
  const handleUpdateProp = useCallback(async (name: string, updates: Partial<Prop>) => {
    if (!currentProjectName) return;
    try {
      await API.updateProjectProp(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_prop_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateProp = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await API.generateProjectProp(currentProjectName, name, currentProjectData?.props?.[name]?.description ?? "");
      useAppStore.getState().pushToast(tRef.current("prop_task_submitted_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: (err as Error).message }), "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddPropSubmit = useCallback(async (name: string, description: string) => {
    if (!currentProjectName) return;
    try {
      await API.addProjectProp(currentProjectName, name, description);
      await refreshProject();
      setAddingProp(false);
      useAppStore.getState().pushToast(tRef.current("prop_added_toast", { name }), "success");
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

      <Route path="/characters">
        <div className="p-4">
          <CharactersPage
            projectName={currentProjectName}
            characters={currentProjectData?.characters ?? {}}
            onSaveCharacter={handleSaveCharacter}
            onGenerateCharacter={handleGenerateCharacterVoid}
            onAddCharacter={() => setAddingCharacter(true)}
            onRestoreCharacterVersion={handleRestoreAsset}
            generatingCharacterNames={generatingCharacterNames}
          />
          {addingCharacter && (
            <AddCharacterForm
              onSubmit={handleAddCharacterSubmit}
              onCancel={() => setAddingCharacter(false)}
            />
          )}
        </div>
      </Route>

      <Route path="/scenes">
        <div className="p-4">
          <ScenesPage
            projectName={currentProjectName}
            scenes={currentProjectData?.scenes ?? {}}
            onUpdateScene={handleUpdateSceneVoid}
            onGenerateScene={handleGenerateSceneVoid}
            onAddScene={() => setAddingScene(true)}
            onRestoreSceneVersion={handleRestoreAsset}
            generatingSceneNames={generatingSceneNames}
          />
          {addingScene && (
            <AddSceneInline
              onSubmit={handleAddSceneSubmit}
              onCancel={() => setAddingScene(false)}
            />
          )}
        </div>
      </Route>

      <Route path="/props">
        <div className="p-4">
          <PropsPage
            projectName={currentProjectName}
            props={currentProjectData?.props ?? {}}
            onUpdateProp={handleUpdatePropVoid}
            onGenerateProp={handleGeneratePropVoid}
            onAddProp={() => setAddingProp(true)}
            onRestorePropVersion={handleRestoreAsset}
            generatingPropNames={generatingPropNames}
          />
          {addingProp && (
            <AddPropInline
              onSubmit={handleAddPropSubmit}
              onCancel={() => setAddingProp(false)}
            />
          )}
        </div>
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
              onUpdatePrompt={voidPromise(handleUpdatePrompt)}
              onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
              onGenerateVideo={voidPromise(handleGenerateVideo)}
              onGenerateGrid={voidPromise(handleGenerateGrid)}
              onRestoreStoryboard={handleRestoreAsset}
              onRestoreVideo={handleRestoreAsset}
            />
          );
        }}
      </Route>
    </Switch>
  );
}

// ---------------------------------------------------------------------------
// CharactersPage -- placeholder grid (Task 37 will replace with GalleryToolbar)
// ---------------------------------------------------------------------------

function CharactersPage(props: {
  projectName: string;
  characters: Record<string, import("@/types").Character>;
  onSaveCharacter: (name: string, payload: { description: string; voiceStyle: string; referenceFile?: File | null }) => Promise<void>;
  onGenerateCharacter: (name: string) => void;
  onAddCharacter: () => void;
  onRestoreCharacterVersion: () => Promise<void> | void;
  generatingCharacterNames: Set<string>;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {Object.entries(props.characters).map(([name, char]) => (
        <CharacterCard
          key={name}
          name={name}
          character={char}
          projectName={props.projectName}
          onSave={props.onSaveCharacter}
          onGenerate={props.onGenerateCharacter}
          onRestoreVersion={props.onRestoreCharacterVersion}
          generating={props.generatingCharacterNames.has(name)}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ScenesPage -- placeholder grid (Task 37 will replace)
// ---------------------------------------------------------------------------

function ScenesPage(props: {
  projectName: string;
  scenes: Record<string, Scene>;
  onUpdateScene: (name: string, updates: Partial<Scene>) => void;
  onGenerateScene: (name: string) => void;
  onAddScene: () => void;
  onRestoreSceneVersion: () => Promise<void> | void;
  generatingSceneNames: Set<string>;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {Object.entries(props.scenes).map(([name, scene]) => (
        <SceneCard
          key={name}
          name={name}
          scene={scene}
          projectName={props.projectName}
          onUpdate={props.onUpdateScene}
          onGenerate={props.onGenerateScene}
          onRestoreVersion={props.onRestoreSceneVersion}
          generating={props.generatingSceneNames.has(name)}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PropsPage -- placeholder grid (Task 37 will replace)
// ---------------------------------------------------------------------------

function PropsPage(props: {
  projectName: string;
  props: Record<string, Prop>;
  onUpdateProp: (name: string, updates: Partial<Prop>) => void;
  onGenerateProp: (name: string) => void;
  onAddProp: () => void;
  onRestorePropVersion: () => Promise<void> | void;
  generatingPropNames: Set<string>;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {Object.entries(props.props).map(([name, prop]) => (
        <PropCard
          key={name}
          name={name}
          prop={prop}
          projectName={props.projectName}
          onUpdate={props.onUpdateProp}
          onGenerate={props.onGenerateProp}
          onRestoreVersion={props.onRestorePropVersion}
          generating={props.generatingPropNames.has(name)}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddSceneInline -- simple inline form for adding a scene
// ---------------------------------------------------------------------------

function AddSceneInline({ onSubmit, onCancel }: { onSubmit: (name: string, description: string) => void; onCancel: () => void }) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  return (
    <div className="mt-4 rounded-xl border border-gray-700 bg-gray-900 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">{t("add_scene")}</h4>
      <input type="text" placeholder={t("name")} value={name} onChange={e => setName(e.target.value)}
        className="mb-2 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200" />
      <textarea placeholder={t("scene_desc_placeholder")} value={desc} onChange={e => setDesc(e.target.value)} rows={2}
        className="mb-3 w-full resize-none rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200" />
      <div className="flex gap-2">
        <button type="button" onClick={() => { if (name.trim()) onSubmit(name.trim(), desc); }}
          className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">{t("common:confirm")}</button>
        <button type="button" onClick={onCancel}
          className="rounded-lg bg-gray-700 px-4 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-600">{t("common:cancel")}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddPropInline -- simple inline form for adding a prop
// ---------------------------------------------------------------------------

function AddPropInline({ onSubmit, onCancel }: { onSubmit: (name: string, description: string) => void; onCancel: () => void }) {
  const { t } = useTranslation("dashboard");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  return (
    <div className="mt-4 rounded-xl border border-gray-700 bg-gray-900 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">{t("add_prop")}</h4>
      <input type="text" placeholder={t("name")} value={name} onChange={e => setName(e.target.value)}
        className="mb-2 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200" />
      <textarea placeholder={t("prop_desc_placeholder")} value={desc} onChange={e => setDesc(e.target.value)} rows={2}
        className="mb-3 w-full resize-none rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200" />
      <div className="flex gap-2">
        <button type="button" onClick={() => { if (name.trim()) onSubmit(name.trim(), desc); }}
          className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">{t("common:confirm")}</button>
        <button type="button" onClick={onCancel}
          className="rounded-lg bg-gray-700 px-4 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-600">{t("common:cancel")}</button>
      </div>
    </div>
  );
}
