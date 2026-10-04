import { useCallback, useRef } from "react";
import { errMsg, voidPromise } from "@/utils/async";
import { Route, Switch, Redirect, useSearchParams } from "wouter";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_EPISODES,
  EPISODE_VIEW_EDIT,
  EPISODE_VIEW_PARAM,
} from "@/app-routes";
import { useTranslation } from "react-i18next";
import { useProjectsStore } from "@/stores/projects-store";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import { DemoEpisodePlaceholder } from "@/onboarding/DemoEpisodePlaceholder";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./OverviewCanvas";
import { EpisodesView } from "./episodes/EpisodesView";
import { CharactersPage } from "./lorebook/CharactersPage";
import { ScenesPage } from "./lorebook/ScenesPage";
import { PropsPage } from "./lorebook/PropsPage";
import { ProductsPage } from "./lorebook/ProductsPage";
import { ReferenceVideoCanvas } from "./reference/ReferenceVideoCanvas";
import { GridImageToVideoCanvas } from "./grid/GridImageToVideoCanvas";
import { EpisodeSourceReview } from "./EpisodeSourceReview";
import { EditTimelineView } from "./edit/EditTimelineView";
import { EditTimelineEmptyState } from "./edit-render/EditTimelineEmptyState";
import { RenderButton } from "./edit-render/RenderButton";
import {
  EPISODE_VIEW_PANEL_ID,
  EpisodeViewSwitch,
  episodeViewOf,
  episodeViewTabId,
  type EpisodeView,
} from "./EpisodeViewSwitch";
import { WorkflowPanel } from "@/components/workflow/WorkflowPanel";
import { API } from "@/api";
import { PromptAuthoringHost } from "@/components/canvas/shared/PromptAuthoringDialog";
import { usePromptAuthoringStore } from "@/stores/prompt-authoring-store";
import { ScriptPlanHost } from "@/components/canvas/shared/ScriptPlanDialog";
import { previewAspect } from "@/utils/preview-aspect";
import { TextTaskFailureNote } from "@/components/canvas/shared/TextTaskFailureNote";
import { AdScriptHost } from "@/components/canvas/shared/AdScriptDialog";
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
import {
  durationOutOfRangeReason,
  useModelCapabilities,
} from "@/hooks/useModelCapabilities";
import { gridStoryboardEnabled, normalizeRoute } from "@/utils/generation-mode";
import type {
  Scene,
  Prop,
  Product,
} from "@/types";
import type { EpisodeScript } from "@/types/script";

/** 集级路由 path，与渲染该集的 `<Route>` 共用一份。 */
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
  const tRef = useRef(t);
  // eslint-disable-next-line react-hooks/refs -- tRef 是稳定 event-handler ref 模式，用于在回调中获取最新 t 而不触发无限 useCallback 重建
  tRef.current = t;
  const { currentProjectData, currentProjectName, currentScripts, projectDetailLoading } =
    useProjectsStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const episodeView = episodeViewOf(searchParams);
  const setEpisodeView = (view: EpisodeView) =>
    setSearchParams((params) => {
      if (view === "edit") params.set(EPISODE_VIEW_PARAM, EPISODE_VIEW_EDIT);
      else params.delete(EPISODE_VIEW_PARAM);
      return params;
    });
  // 演示态：资产画布仍走 readOnly 透传，工作台时间线的只读则由组件自己直读同一判定。
  // useDemoWorkbench() 已把路由参数与 store 的判定滞后收口在单一来源，此处直接消费。
  const demoMode = useDemoWorkbench();

  // 演示态不发真实能力请求。demoMode 演示→真实切换时先于 store 变为 false，
  // currentProjectName 单独判一次兜住这一帧仍读到旧演示项目名的窗口。
  const capabilitiesEnabled = !demoMode && !isDemoProject(currentProjectName);

  // 逐个分镜的时长编辑器候选取经联动约束收窄后的集合，用户就选不到入队后必然被拒的组合；
  // 已保存的越界值不改写，由 ShotDetail 按成因给警告并引导重选。
  // 不传候选模型、分辨率与参考图路径：服务端按项目生成模式解析实际执行的
  // i2v/r2v 桶模型及其已保存档位。传项目默认 `video_backend` 会覆盖细分桶、与执行期错位。
  // 能力按项目生成模式定轴、全项目同一口径，故不带集号。
  const capabilities = useModelCapabilities({
    projectName: currentProjectName,
    enabled: capabilitiesEnabled,
  });

  // 从任务队列派生 loading 状态（替代本地 state）：活跃 + 最新行胜出两条不变量下沉到 store selector
  const generatingCharacterNames = useActiveResourceIds("character", currentProjectName);
  const generatingSceneNames = useActiveResourceIds("scene", currentProjectName);
  const generatingPropNames = useActiveResourceIds("prop", currentProjectName);
  const generatingProductNames = useActiveResourceIds("product", currentProjectName);

  // 刷新项目数据；返回本地 store 是否已同步成功，供调用方决定是否推进依赖新顺序的 UI 状态。
  // 在途合并 + 失败留旧收敛于 projects-store 的 refreshProject，此处仅表达意图。
  // "cancelled"（项目切换取消域轮换等）与 "failed" 在这里都不算已同步，统一按 false
  // 处理——本地调用方只用这个值决定是否推进 UI 状态，不弹错误提示，故无需再细分。
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

  // ---- Timeline action callbacks ----
  // These receive scriptFile from TimelineCanvas so they always use the active episode's script.
  // 返回是否写入成功：本函数内部吞掉异常并转 toast，调用方靠返回值而非
  // "是否抛出"判断能否清空本地草稿——不依赖此契约的调用方
  // （TimelineCanvas / GridImageToVideoCanvas）按 void 用即可，多出的返回值不影响它们。
  const handleUpdatePrompt = useCallback(async (
    segmentId: string,
    fieldOrPatch: string | Record<string, unknown>,
    value?: unknown,
    scriptFile?: string,
  ): Promise<boolean> => {
    if (!currentProjectName) return false;
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
      // 仅在本地 store 已同步成功时报告成功：PATCH 已落库但刷新失败/取消时 store
      // 仍是旧剧本，此时报告成功会让调用方清空草稿却回显旧值——与 handleMoveShot 同一契约。
      return await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_prompt_failed", { message: errMsg(err) }), "error");
      return false;
    }
  }, [currentProjectName, currentProjectData, refreshProject]);

  // 不走 voidPromise（见其 JSDoc）：ShotDetail.handleSave / handleRefsSave 靠
  // await 这个回调维持保存中状态——真正要丢弃的只是布尔返回值，等待本身必须
  // 原样保留。TimelineCanvas 与 GridImageToVideoCanvas 均不消费返回值，共用
  // 同一适配回调。
  const awaitedUpdatePrompt = useCallback(
    async (...args: Parameters<typeof handleUpdatePrompt>) => {
      await handleUpdatePrompt(...args);
    },
    [handleUpdatePrompt],
  );

  // 分镜改序（各形态通用）：把分镜移到 afterId 之后，null 移到最前。
  // 返回是否移动成功，供编辑器把选中态跟随到分镜的新位置。
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
      // 仅在本地 store 已写回新顺序时报告成功：刷新失败时 segments 仍是旧序，
      // 此时让选中态跟随新位置会静默切到别的分镜。
      return await refreshProject();
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("reorder_shot_failed", { message: errMsg(err) }), "error");
      return false;
    }
  }, [currentProjectName, currentScripts, refreshProject]);

  // 时间线新增 / 移除分镜：服务端按当前剧本 revision 执行，这里不取快照。
  // 写入一经提交即报告成功：随后的本地刷新失败时只提示重新加载，不让调用方保持可重试，
  // 否则重试会再新增一条分镜或对已移除的分镜再发一次移除。
  const refreshAfterStructureEdit = useCallback(async () => {
    if (!(await refreshProject())) {
      useAppStore.getState().pushToast(tRef.current("shot_structure_refresh_failed"), "warning");
    }
  }, [refreshProject]);

  // afterId 为 null 时追加到末尾（空脚本里即第一条）。
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
      useAppStore.getState().pushToast(tRef.current("shot_insert_failed", { message: errMsg(err) }), "error");
      return false;
    }
    await refreshAfterStructureEdit();
    return true;
  }, [currentProjectName, currentScripts, refreshAfterStructureEdit]);

  const handleRemoveShot = useCallback(async (itemId: string, scriptFile?: string): Promise<boolean> => {
    if (!currentProjectName || !currentScripts) return false;
    const resolvedFile = scriptFile ?? Object.keys(currentScripts)[0];
    if (!resolvedFile) return false;
    try {
      await API.removeScriptItem(currentProjectName, itemId, resolvedFile);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("shot_remove_failed", { message: errMsg(err) }), "error");
      return false;
    }
    await refreshAfterStructureEdit();
    return true;
  }, [currentProjectName, currentScripts, refreshAfterStructureEdit]);

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

  // 后期配音项目不生成旁白配音：收起画布上的生成入口，已有配音照常试听。
  const narrationGenerationEnabled = currentProjectData?.narration_delivery === "use_tts";

  // ---- Workflow panel callbacks ----
  // 面板只陈述状态，动作交回既有入口执行：跳转复用 Agent 定位用的同一条 scrollTarget 缝，
  // 重生复用本组件已有的入队回调。面板不自建播放器，也不自建入队路径。
  const handleViewWorkflowUnit = useCallback((unitId: string) => {
    useAppStore.getState().triggerScrollTo({
      type: normalizeRoute(currentProjectData?.generation_mode) === "reference_video"
        ? "reference_unit"
        : "segment",
      id: unitId,
    });
  }, [currentProjectData?.generation_mode]);

  // 剧本文件由调用方按当前剧集给出：多集项目里 currentScripts 装着全部剧集，
  // 取第一个键会把重生打到别集的剧本上，用户看到的是另一集被重做。
  const handleWorkflowRegenerate = useCallback(async (
    stepId: string,
    unitIds: string[],
    scriptFile: string,
  ) => {
    if (!currentProjectName || !currentScripts) return;
    for (const unitId of unitIds) {
      try {
        if (stepId === "storyboard") {
          await handleGenerateStoryboard(unitId, scriptFile);
        } else if (stepId === "video") {
          await handleGenerateVideo(unitId, scriptFile);
        }
      } catch (err) {
        useAppStore.getState().pushToast(tRef.current("generate_video_failed", { message: errMsg(err) }), "error");
      }
    }
  }, [
    currentProjectName,
    currentScripts,
    handleGenerateStoryboard,
    handleGenerateVideo,
  ]);

  // ---- Character CRUD callbacks ----
  const handleSaveCharacter = useCallback(async (
    name: string,
    payload: {
      description: string;
      voiceStyle: string;
      referenceFile?: File | null;
      audioFile?: File | null;
    },
  ) => {
    if (!currentProjectName) return;
    const invalidateKeys = payload.referenceFile || payload.audioFile
      ? [buildEntityRevisionKey("character", name)]
      : [];
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

      if (payload.audioFile) {
        await API.uploadFile(
          currentProjectName,
          "character_audio_ref",
          payload.audioFile,
          name,
        );
      }

      useAppStore.getState().pushToast(tRef.current("character_updated_toast", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("update_character_failed", { message: errMsg(err) }), "error");
    } finally {
      // 三步写操作顺序执行，任一步失败时前面已持久化的变更（描述/参考图）也要反映到本地
      // store，否则用户会误以为整个保存失败而重复提交
      await refreshProject(invalidateKeys);
    }
  }, [currentProjectName, refreshProject]);

  const handleGenerateCharacter = useCallback(async (name: string) => {
    if (!currentProjectName) return;
    try {
      await enqueueCharacter(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName]);

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
      await enqueueScene(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName]);

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
      await enqueueProp(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName]);

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
      await enqueueProduct(currentProjectName, name);
    } catch (err) {
      useAppStore.getState().pushToast(tRef.current("submit_failed", { message: errMsg(err) }), "error");
    }
  }, [currentProjectName]);

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

  // `currentProjectName` 在详情到达前就已落地（见 router.tsx 首屏加载的注释），
  // 仅查它会在深链（/characters 等）直接打开或详情较慢时把空集合渲染成可交互的
  // 「空项目」页面；`projectDetailLoading` 才是详情是否已到达的信号。
  if (!currentProjectName || projectDetailLoading) {
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
          readOnly={demoMode}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_EPISODES}`}>
        {/* 演示项目后端不存在，广告/短片恒单集、不经分集；侧栏已隐藏入口，这里兜底直接输入 URL 的情形 */}
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
          onSaveCharacter={handleSaveCharacter}
          onGenerateCharacter={handleGenerateCharacterVoid}
          onAddCharacter={handleAddCharacterSubmit}
          onRestoreCharacterVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingCharacterNames={generatingCharacterNames}
          voiceBinding={currentProjectData?.character_voice_binding}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_SCENES}`}>
        <ScenesPage
          key={currentProjectName}
          projectName={currentProjectName}
          scenes={currentProjectData?.scenes ?? {}}
          readOnly={demoMode}
          onUpdateScene={handleUpdateSceneVoid}
          onGenerateScene={handleGenerateSceneVoid}
          onAddScene={handleAddSceneSubmit}
          onRestoreSceneVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingSceneNames={generatingSceneNames}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_PROPS}`}>
        <PropsPage
          key={currentProjectName}
          projectName={currentProjectName}
          props={currentProjectData?.props ?? {}}
          readOnly={demoMode}
          onUpdateProp={handleUpdatePropVoid}
          onGenerateProp={handleGeneratePropVoid}
          onAddProp={handleAddPropSubmit}
          onRestorePropVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingPropNames={generatingPropNames}
        />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_PRODUCTS}`}>
        <ProductsPage
          key={currentProjectName}
          projectName={currentProjectName}
          products={currentProjectData?.products ?? {}}
          readOnly={demoMode}
          onUpdateProduct={handleUpdateProductVoid}
          onGenerateProduct={handleGenerateProductVoid}
          onAddProduct={handleAddProductSubmit}
          onRestoreProductVersion={handleRestoreAsset}
          onRefreshProject={refreshProject}
          generatingProductNames={generatingProductNames}
        />
      </Route>

      <Route path={EPISODE_ROUTE_PATH}>
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find((e) => e.episode === epNum);
          const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
          const script = scriptFile ? (currentScripts[scriptFile] ?? null) : null;
          const route = normalizeRoute(currentProjectData?.generation_mode);
          // 服务端已按项目生成模式（是否走参考图路径）与已保存分辨率收窄。
          // reference_video 的参考图约束按 unit 而非按集生效：每个 unit 落哪个桶、可选哪些档位
          // 由服务端按可用参考图逐单元判定，随单元列表到达（reference-video-store），不从这里下发。
          const durationOptions = capabilities.supportedDurations ?? undefined;
          // 内容确认页按剧本规划档位选时长：端点固定时 supportedDurations 为空，规划仍有借用档位。
          const planDurationOptions = capabilities.planningDurations ?? undefined;
          const durationWarningReason = (seconds: number) =>
            durationOutOfRangeReason(seconds, capabilities);
          // 档位空集的两种成因说给用户听的不是同一句：型号没登记时长 vs 这份 workflow 自己定片长。
          const durationEndpointFixed = capabilities.durationEndpointFixed;
          const hasDraft =
            episode?.script_status === "segmented" || episode?.script_status === "generated";
          const isAd = currentProjectData?.content_mode === "ad";

          // 已选集但剧本未生成：进入脚本规划视图（narration/drama 全部生成路径——
          // reference_video 此时 units 为空，同样没有可展示内容）；ad 恒单集无源文
          // 切片，走各自画布。
          // 演示项目没有源文可切片，缺剧本的分集直接说明「演示只做到第 1 集」
          const showSourceReview =
            Boolean(episode) && !script && !hasDraft && !isAd && !demoMode;
          // 剪辑视图预览按剪辑时间线拼接的视频，需要本集已有正式脚本；演示态不提供。
          const canEdit = Boolean(script) && !demoMode;
          const showEditView = canEdit && episodeView === "edit";

          return (
            <div className="flex h-full flex-col">
              {/* 演示态没有真实项目事实可投影，面板不挂载。 */}
              {!demoMode && currentProjectName && (
                <WorkflowPanel
                  projectName={currentProjectName}
                  episode={epNum}
                  onViewUnit={handleViewWorkflowUnit}
                  // 参考生视频的视频入队由 ReferenceVideoCanvas 自己的整批准入判定路径承担，
                  // 本组件的逐单元回调对 video_units 剧本解不出提示词、按下去毫无反应。
                  // 该生成模式只给「查看」跳转，重生入口在跳过去的那张单元卡上。
                  onRegenerate={
                    route === "reference_video" || !scriptFile
                      ? undefined
                      : (stepId, unitIds) =>
                          void handleWorkflowRegenerate(stepId, unitIds, scriptFile)
                  }
                  onAuthorPrompts={
                    script
                      ? () => usePromptAuthoringStore.getState().open({ projectName: currentProjectName, episode: epNum, scope: "pending" })
                      : undefined
                  }
                />
              )}
              {!demoMode && currentProjectName && (
                <TextTaskFailureNote
                  projectName={currentProjectName}
                  episode={epNum}
                  isAd={isAd}
                  hasScript={Boolean(script)}
                />
              )}
              {!demoMode && currentProjectName && (
                <ScriptPlanHost
                  projectName={currentProjectName}
                  episode={epNum}
                  savedInstructions={episode?.script_plan_instructions}
                />
              )}
              {!demoMode && currentProjectName && isAd && (
                <AdScriptHost projectName={currentProjectName} episode={epNum} />
              )}
              {!demoMode && currentProjectName && (
                <PromptAuthoringHost
                  projectName={currentProjectName}
                  episode={epNum}
                  script={script}
                  savedInstructions={episode?.prompt_authoring_instructions}
                />
              )}
              {canEdit && <EpisodeViewSwitch view={episodeView} onChange={setEpisodeView} />}
              <div
                className="min-h-0 flex-1"
                role={canEdit ? "tabpanel" : undefined}
                id={canEdit ? EPISODE_VIEW_PANEL_ID : undefined}
                aria-labelledby={canEdit ? episodeViewTabId(episodeView) : undefined}
              >
                {showEditView ? (
                  <EditTimelineView
                    key={`${currentProjectName}::${epNum}`}
                    projectName={currentProjectName}
                    episode={epNum}
                    script={script}
                    aspect={previewAspect(currentProjectData)}
                    ttsNarration={currentProjectData?.narration_delivery === "use_tts"}
                    renderActions={({ timelineId, timelineName, issues, showIssues }) =>
                      // 读取完成前不知道有没有阻断级 issue，先不给出片入口。
                      issues === null ? null : (
                        <RenderButton
                          projectName={currentProjectName}
                          timelineId={timelineId}
                          timelineName={timelineName}
                          issues={issues}
                          narrationAvailable={currentProjectData?.narration_delivery === "use_tts"}
                          onShowIssues={showIssues}
                        />
                      )
                    }
                    renderEmptyState={({ reload }) => (
                      <EditTimelineEmptyState
                        projectName={currentProjectName}
                        episode={epNum}
                        onCreated={reload}
                      />
                    )}
                  />
                ) : demoMode && !script ? (
                  <DemoEpisodePlaceholder />
                ) : showSourceReview && episode ? (
                  <EpisodeSourceReview
                    projectName={currentProjectName}
                    episode={epNum}
                    episodes={currentProjectData?.episodes ?? []}
                  />
                ) : route === "reference_video" ? (
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
                    episodeTitle={episode?.title}
                    onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
                    canEditTitle={Boolean(episode?.script_file)}
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
                    onUpdatePrompt={awaitedUpdatePrompt}
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
                    // 和 ReferenceVideoCanvas (上方) 同理：同 epNum 跨项目不 remount
                    // 会让 TimelineCanvas 内部的 useState / useRef（选中 scene、草稿缓冲、
                    // 滚动位置等）残留上一个项目的值。key 带上 projectName 天然按项目隔离。
                    key={`${currentProjectName}::${epNum}`}
                    projectName={currentProjectName}
                    episode={epNum}
                    episodeTitle={episode?.title}
                    // 演示态的只读由 TimelineCanvas 直读判定收口，这里只表达非演示态的固有约束
                    onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
                    canEditTitle={Boolean(episode?.script_file)}
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
                    onUpdatePrompt={awaitedUpdatePrompt}
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
              </div>
            </div>
          );
        }}
      </Route>
    </Switch>
  );
}
