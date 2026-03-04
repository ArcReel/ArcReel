import { useState, useCallback, useMemo } from "react";
import { Route, Switch, Redirect, useLocation } from "wouter";
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
import type { Clue } from "@/types";

// ---------------------------------------------------------------------------
// StudioCanvasRouter — reads Zustand store data and renders the correct
// canvas view based on the nested route within /app/projects/:projectName.
// ---------------------------------------------------------------------------

export function StudioCanvasRouter() {
  const { currentProjectData, currentProjectName, currentScripts } =
    useProjectsStore();

  const [addingCharacter, setAddingCharacter] = useState(false);
  const [addingClue, setAddingClue] = useState(false);

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
  const refreshProject = useCallback(async (invalidateMedia: boolean = false) => {
    if (!currentProjectName) return;
    try {
      const res = await API.getProject(currentProjectName);
      useProjectsStore.getState().setCurrentProject(
        currentProjectName,
        res.project,
        res.scripts ?? {},
      );
      if (invalidateMedia) {
        useAppStore.getState().invalidateMediaAssets();
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
      useAppStore.getState().pushToast(`更新 Prompt 失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, currentProjectData, refreshProject]);

  const handleGenerateStoryboard = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    const script = currentScripts[resolvedFile];
    if (!script) return;
    const segments = ("segments" in script ? script.segments : undefined) ??
                     ("scenes" in script ? script.scenes : undefined) ?? [];
    const seg = segments.find((s) => {
      const id = "segment_id" in s ? s.segment_id : (s as { scene_id?: string }).scene_id ?? "";
      return id === segmentId;
    });
    const prompt = seg?.image_prompt ?? "";
    try {
      await API.generateStoryboard(currentProjectName, segmentId, prompt as string | Record<string, unknown>, resolvedFile);
      useAppStore.getState().pushToast(`已提交分镜 "${segmentId}" 生成任务`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`生成分镜失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, currentScripts]);

  const handleGenerateVideo = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    const script = currentScripts[resolvedFile];
    if (!script) return;
    const segments = ("segments" in script ? script.segments : undefined) ??
                     ("scenes" in script ? script.scenes : undefined) ?? [];
    const seg = segments.find((s) => {
      const id = "segment_id" in s ? s.segment_id : (s as { scene_id?: string }).scene_id ?? "";
      return id === segmentId;
    });
    const prompt = seg?.video_prompt ?? "";
    const duration = seg?.duration_seconds ?? 4;
    try {
      await API.generateVideo(currentProjectName, segmentId, prompt as string | Record<string, unknown>, resolvedFile, duration);
      useAppStore.getState().pushToast(`已提交视频 "${segmentId}" 生成任务`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`生成视频失败: ${(err as Error).message}`, "error");
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

      await refreshProject(Boolean(payload.referenceFile));
      useAppStore.getState().pushToast(`角色 "${name}" 已更新`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`更新角色失败: ${(err as Error).message}`, "error");
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
        .pushToast(`角色 "${name}" 生成任务已提交`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`提交失败: ${(err as Error).message}`, "error");
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

      await refreshProject(Boolean(referenceFile));
      setAddingCharacter(false);
      useAppStore.getState().pushToast(`角色 "${name}" 已添加`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`添加失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, refreshProject]);

  // ---- Clue CRUD callbacks ----
  const handleUpdateClue = useCallback(async (name: string, updates: Partial<Clue>) => {
    if (!currentProjectName) return;
    try {
      await API.updateClue(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(`更新线索失败: ${(err as Error).message}`, "error");
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
        .pushToast(`线索 "${name}" 生成任务已提交`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`提交失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddClueSubmit = useCallback(async (name: string, clueType: string, description: string, importance: string) => {
    if (!currentProjectName) return;
    try {
      await API.addClue(currentProjectName, name, clueType, description, importance);
      await refreshProject();
      setAddingClue(false);
      useAppStore.getState().pushToast(`线索 "${name}" 已添加`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`添加失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleRestoreAsset = useCallback(async () => {
    await refreshProject(true);
  }, [refreshProject]);

  const [location] = useLocation();

  if (!currentProjectName) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        加载中...
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

          return (
            <TimelineCanvas
              projectName={currentProjectName}
              episodeScript={script}
              scriptFile={scriptFile ?? undefined}
              projectData={currentProjectData}
              onUpdatePrompt={handleUpdatePrompt}
              onGenerateStoryboard={handleGenerateStoryboard}
              onGenerateVideo={handleGenerateVideo}
              onRestoreStoryboard={handleRestoreAsset}
              onRestoreVideo={handleRestoreAsset}
            />
          );
        }}
      </Route>
    </Switch>
  );
}
