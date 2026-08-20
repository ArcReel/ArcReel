import { useState, useCallback, useEffect, useRef } from "react";
import { errMsg, voidPromise } from "@/utils/async";
import { Route, Switch, Redirect } from "wouter";
import {
  WORKSPACE_ROUTE_LOREBOOK,
  WORKSPACE_ROUTE_CLUES,
  WORKSPACE_ROUTE_SOURCE,
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_SCENES,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_PRODUCTS,
} from "@/app-routes";
import { useTranslation } from "react-i18next";
import { useProjectsStore } from "@/stores/projects-store";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import { DemoEpisodePlaceholder } from "@/onboarding/DemoEpisodePlaceholder";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { useCurrentEpisode, EPISODE_ROUTE_PATH } from "@/hooks/useCurrentEpisode";
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
import { WorkflowPanel } from "@/components/workflow/WorkflowPanel";
import { API, NarratedVideoDurationError } from "@/api";
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
  getProviderModels,
  getCustomProviderModels,
  lookupProjectVideoResolution,
} from "@/utils/provider-models";
import {
  durationOutOfRangeReason,
  narrowDurations,
  useModelCapabilities,
} from "@/hooks/useModelCapabilities";
import { gridStoryboardEnabled, normalizeRoute } from "@/utils/generation-mode";
import type {
  Scene,
  Prop,
  Product,
  CustomProviderInfo,
  ProviderInfo,
  ReferenceGenerationRequestOptions,
} from "@/types";
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
  // 演示态：资产画布仍走 readOnly 透传，工作台时间线的只读则由组件自己直读同一判定。
  // useDemoWorkbench() 已把路由参数与 store 的判定滞后收口在单一来源，此处直接消费。
  const demoMode = useDemoWorkbench();

  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProviderInfo[]>([]);
  const [globalVideoBackend, setGlobalVideoBackend] = useState("");

  // 目录侧与服务端侧听同一个失效信号：本组件跨路由原地复用，改完全局默认后端 / 自定义供应商
  // 回到工作台不会重挂载，只重取服务端能力而留着旧目录的话，时长仍按旧配置解析。
  const capabilitiesRevision = useCapabilitiesStore((s) => s.revision);

  useEffect(() => {
    // 这三份数据只服务视频时长选项。演示态的时长是虚构的静态展示，唯一还会用到选项的
    // 是「时长与后端不兼容」的橙色标记——对虚构数据那是伪告警，不值得为它发三个全局请求。
    if (demoMode) return;
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
  }, [demoMode, capabilitiesRevision]);

  // 演示态即便 state 里还留着上一个真实项目的 providers/backend（同一组件实例原地切入演示
  // 路由，effect 提前 return 不会清掉旧值），也不参与解析——否则真实后端的时长限制会继续套用
  // 到演示的虚构时长上，触发「不兼容」误报。demoMode 演示→真实切换时先于 store 变为 false，
  // currentProjectName 单独判一次兜住这一帧仍读到旧演示项目名的窗口。
  const capabilitiesEnabled = !demoMode && !isDemoProject(currentProjectName);

  // 逐镜头时长编辑器的候选取经联动约束收窄后的集合，用户就选不到入队后必然被拒的组合；
  // 已保存的越界值不改写，由 ShotDetail 按成因给警告并引导重选。
  // 后端未配置时能力管线退回服务端解析出的 model（与生成路径同一套规则，避免 FE/BE 漂移）。
  //
  // 收窄放在下方按集的 Route 渲染里：那里才有本集的分辨率与剧本上下文，而能力查询只在组件
  // 顶层做一次。此处只取不随上下文变化的两项。
  //
  // 服务端侧把当前集号一并带上，让 voiceConsistency / firstFrame 等派生值与执行层同口径。
  // 集号在顶层从路由取（`useCurrentEpisode` 与下方 Route 共用同一份 path），能力查询因此
  // 仍只发生一次、随切集重取。
  const currentEpisode = useCurrentEpisode();
  const effectiveVideoBackend = currentProjectData?.video_backend || globalVideoBackend;
  const { rawDurations, durationConstraints, resolvedVideoBackend } = useModelCapabilities({
    projectName: currentProjectName,
    videoBackend: effectiveVideoBackend,
    episode: currentEpisode,
    providers,
    customProviders,
    enabled: capabilitiesEnabled,
  });
  // 分辨率按能力管线解析出的 `provider/model` 查，而非传入的原始值：后者可能是裸 provider
  // （服务端补全默认视频模型）或留空跟随全局默认，直接拿去查 `model_settings` 会把 provider ID
  // 当成 model ID、读不到用户实际保存的档位，于是该收窄的候选照旧呈现。
  // 用户未选分辨率时为 null → 不收窄：执行期省略 resolution 参数、供应商按自己的默认档位处理，
  // 该档位下全集本就合法。与后端约束求值、项目设置页的时长选择器同口径。
  const videoResolution = lookupProjectVideoResolution(
    currentProjectData,
    resolvedVideoBackend || effectiveVideoBackend,
  );

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

  const handleGenerateVideo = useCallback(async (
    segmentId: string,
    scriptFile?: string,
    requestOptions?: ReferenceGenerationRequestOptions,
  ) => {
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
        requestOptions,
      );
    } catch (err) {
      if (err instanceof NarratedVideoDurationError) throw err;
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
        } else if (stepId === "narration_delivery") {
          await handleGenerateNarration(unitId, scriptFile);
        }
      } catch (err) {
        // 时长档位确认只在单元卡自己的确认弹窗里发生，面板不复刻这套流程——
        // 把用户带到那张卡上完成确认，而不是甩出一句没有下文的裸错误。
        if (err instanceof NarratedVideoDurationError) {
          useAppStore.getState().pushToast(tRef.current("workflow_regenerate_needs_confirmation"), "error");
          handleViewWorkflowUnit(unitId);
          continue;
        }
        useAppStore.getState().pushToast(tRef.current("generate_video_failed", { message: errMsg(err) }), "error");
      }
    }
  }, [
    currentProjectName,
    currentScripts,
    handleGenerateStoryboard,
    handleGenerateVideo,
    handleGenerateNarration,
    handleViewWorkflowUnit,
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

      <Route path={`/${WORKSPACE_ROUTE_LOREBOOK}`}>
        <Redirect to={`/${WORKSPACE_ROUTE_CHARACTERS}`} />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_CLUES}`}>
        <Redirect to={`/${WORKSPACE_ROUTE_SCENES}`} />
      </Route>

      <Route path={`/${WORKSPACE_ROUTE_SOURCE}`}>
        {/* 演示项目没有源文件、后端也不存在该项目；侧栏已隐藏该入口，这里再兜底直接输入 URL 的情形 */}
        {demoMode ? <Redirect to="/" /> : <SourceFilesPage projectName={currentProjectName} />}
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

      <Route path={`/${WORKSPACE_ROUTE_SOURCE}/:filename`}>
        {(params) =>
          demoMode ? (
            <Redirect to="/" />
          ) : (
            <SourceFileViewer
              projectName={currentProjectName}
              filename={decodeURIComponent(params.filename)}
            />
          )
        }
      </Route>

      <Route path={EPISODE_ROUTE_PATH}>
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find((e) => e.episode === epNum);
          const scriptFile = episode?.script_file?.replace(/^scripts\//, "");
          const script = scriptFile ? (currentScripts[scriptFile] ?? null) : null;
          const route = normalizeRoute(currentProjectData?.generation_mode);
          // 路线决定是否走参考图路径，时长候选据此收窄。
          const durationCtx = {
            videoResolution,
            usesReferenceImages: route === "reference_video",
          };
          const durationOptions =
            narrowDurations({ rawDurations, durationConstraints }, durationCtx) ?? undefined;
          // reference_video 的参考图约束是按 unit 而非按集生效（同集内不带 references 的
          // unit 不受此约束，见 lib.reference_video.request_projection 的
          // ReferenceUnitRequestProjector 按可用参考图定 r2v / i2v 的判据）：多备一份不叠加
          // 参考图收窄的档位，供画布按每个 unit 自己的引用状态选用。
          const durationOptionsNoReference =
            narrowDurations({ rawDurations, durationConstraints }, { videoResolution }) ??
            undefined;
          const durationWarningReason = (seconds: number) =>
            durationOutOfRangeReason(
              seconds,
              { rawDurations, supportedDurations: durationOptions ?? null, durationConstraints },
              durationCtx,
            );
          const hasDraft =
            episode?.script_status === "segmented" || episode?.script_status === "generated";
          const isAd = currentProjectData?.content_mode === "ad";

          // 已选集但剧本未生成：进入内容整理视图（narration/drama 全部生成路径——
          // reference_video 此时 units 为空，同样没有可展示内容）；ad 恒单集无源文
          // 切片，走各自画布。
          // 演示项目没有源文可切片，缺剧本的分集直接说明「演示只做到第 1 集」
          const showSourceReview =
            Boolean(episode) && !script && !hasDraft && !isAd && !demoMode;

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
                  // 该路线只给「查看」跳转，重生入口在跳过去的那张单元卡上。
                  onRegenerate={
                    route === "reference_video" || !scriptFile
                      ? undefined
                      : (stepId, unitIds) =>
                          void handleWorkflowRegenerate(stepId, unitIds, scriptFile)
                  }
                />
              )}
              <div className="min-h-0 flex-1">
                {demoMode && !script ? (
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
                    // unit 时长档位随所选模型能力变化（已按本集参考图路径收窄）
                    durationOptions={durationOptions}
                    durationOptionsNoReference={durationOptionsNoReference}
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
                    durationWarningReason={durationWarningReason}
                    onUpdatePrompt={awaitedUpdatePrompt}
                    onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
                    onGenerateVideo={handleGenerateVideo}
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
                    // 演示态的只读由 TimelineCanvas 直读判定收口，这里只表达非演示态的固有约束
                    onSaveTitle={(title) => handleUpdateEpisodeTitle(epNum, title)}
                    canEditTitle={Boolean(episode?.script_file)}
                    hasDraft={hasDraft}
                    episodeScript={script}
                    scriptFile={scriptFile ?? undefined}
                    projectData={currentProjectData}
                    durationOptions={durationOptions}
                    durationWarningReason={durationWarningReason}
                    onUpdatePrompt={awaitedUpdatePrompt}
                    onMoveShot={isAd ? handleMoveShot : undefined}
                    onGenerateStoryboard={voidPromise(handleGenerateStoryboard)}
                    onGenerateVideo={handleGenerateVideo}
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
