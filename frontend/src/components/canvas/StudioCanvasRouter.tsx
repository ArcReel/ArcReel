import { useState, useCallback } from "react";
import { Route, Switch, Redirect, useLocation } from "wouter";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { LorebookGallery } from "./lorebook/LorebookGallery";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./OverviewCanvas";
import { SourceFileViewer } from "./SourceFileViewer";
import { AddCharacterForm } from "./lorebook/AddCharacterForm";
import { AddClueForm } from "./lorebook/AddClueForm";
import { API } from "@/api";
import type { Character, Clue } from "@/types";

// ---------------------------------------------------------------------------
// StudioCanvasRouter — reads Zustand store data and renders the correct
// canvas view based on the nested route within /app/projects/:projectName.
// ---------------------------------------------------------------------------

export function StudioCanvasRouter() {
  const { currentProjectData, currentProjectName, currentScripts } =
    useProjectsStore();

  const [addingCharacter, setAddingCharacter] = useState(false);
  const [addingClue, setAddingClue] = useState(false);

  // 刷新项目数据
  const refreshProject = useCallback(async () => {
    if (!currentProjectName) return;
    try {
      const res = await API.getProject(currentProjectName);
      useProjectsStore.getState().setCurrentProject(
        currentProjectName,
        res.project,
        res.scripts ?? {},
      );
    } catch {
      // 静默失败
    }
  }, [currentProjectName]);

  // ---- Timeline action callbacks ----
  // These receive scriptFile from TimelineCanvas so they always use the active episode's script.
  const handleUpdatePrompt = useCallback(async (segmentId: string, field: string, value: unknown, scriptFile?: string) => {
    if (!currentProjectName) return;
    try {
      await API.updateSegment(currentProjectName, segmentId, { script_file: scriptFile, [field]: value });
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(`更新 Prompt 失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateStoryboard = useCallback(async (segmentId: string, scriptFile?: string) => {
    if (!currentProjectName || !currentScripts) return;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return;
    const script = currentScripts[resolvedFile] ?? currentScripts[resolvedFile.replace(/^scripts\//, "")];
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
    const script = currentScripts[resolvedFile] ?? currentScripts[resolvedFile.replace(/^scripts\//, "")];
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
  const handleUpdateCharacter = useCallback(async (name: string, updates: Partial<Character>) => {
    if (!currentProjectName) return;
    try {
      await API.updateCharacter(currentProjectName, name, updates);
      await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(`更新角色失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateCharacter = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await API.generateCharacter(currentProjectName, name, currentProjectData?.characters?.[name]?.description ?? "");
      useAppStore.getState().pushToast(`已提交角色 "${name}" 设计图生成任务`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`生成失败: ${(err as Error).message}`, "error");
    }
  }, [currentProjectName, currentProjectData]);

  const handleAddCharacterSubmit = useCallback(async (name: string, description: string, voiceStyle: string) => {
    if (!currentProjectName) return;
    try {
      await API.addCharacter(currentProjectName, name, description, voiceStyle);
      await refreshProject();
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
      await API.generateClue(currentProjectName, name, currentProjectData?.clues?.[name]?.description ?? "");
      useAppStore.getState().pushToast(`已提交线索 "${name}" 设计图生成任务`, "success");
    } catch (err) {
      useAppStore.getState().pushToast(`生成失败: ${(err as Error).message}`, "error");
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
            onUpdateCharacter={handleUpdateCharacter}
            onUpdateClue={handleUpdateClue}
            onGenerateCharacter={handleGenerateCharacter}
            onGenerateClue={handleGenerateClue}
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
          const scriptFile = episode?.script_file;
          // Backend strips "scripts/" prefix from keys, so try both forms
          const script = scriptFile
            ? (currentScripts[scriptFile] ??
               currentScripts[scriptFile.replace(/^scripts\//, "")] ??
               null)
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
            />
          );
        }}
      </Route>
    </Switch>
  );
}
