import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ListChecks } from "lucide-react";
import { useLocation } from "wouter";
import { API } from "@/api";
import {
  draftRepairResourceId,
  enqueueAdScript,
  enqueueDraftRepair,
  enqueuePromptAuthoring,
  promptAuthoringResourceId,
} from "@/actions/generation";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { AssetSheetBatchDialog } from "@/components/canvas/lorebook/AssetSheetBatchDialog";
import { StoryboardBatchDialog } from "@/components/canvas/timeline/StoryboardBatchDialog";
import { createScriptEditTimeline } from "@/components/canvas/edit-render/create-script-timeline";
import { promptAuthoringHandoffText } from "@/components/canvas/shared/prompt-authoring-handoff";
import { DiscardDraftDialog, draftFallbackText, draftFixRequestText, prefillAssistant } from "@/components/shared/DraftStatus";
import { diagnosticCode } from "@/hooks/useDraftEditor";
import { useEpisodeLedger } from "@/hooks/useEpisodeLedger";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useAdScriptStore } from "@/stores/ad-script-store";
import { useAppStore } from "@/stores/app-store";
import { useEpisodeSurfaceStore } from "@/stores/episode-surface-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useScriptPlanStore } from "@/stores/script-plan-store";
import { isResourceBusy, useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import type { ProjectData, StoryboardBatchKind } from "@/types";
import type { EpisodeEditOverview } from "@/types/edit-timeline";
import type { DraftDocType } from "@/types/reference-video";
import { errMsg } from "@/utils/async";
import { episodeAgentRef } from "@/utils/episode-display";
import {
  WORKSPACE_ROUTE_CHARACTERS,
  WORKSPACE_ROUTE_PRODUCTS,
  WORKSPACE_ROUTE_PROPS,
  WORKSPACE_ROUTE_SCENES,
} from "@/app-routes";
import { ProblemList } from "./ProblemList";
import { StepListRow } from "./StepListRow";
import { blockerViews, nextStepForAction } from "./problem-views";
import { buildStepList, type StepAct } from "./step-list";

/**
 * 任务指纹变化到发起重新求解之间的合并窗口（毫秒）。
 *
 * 一批任务入队/开跑/落地时状态是逐条跳的，每跳一次就求解一次计划，等于为同一批生成
 * 打出十几个 `POST /workflow-plan`。窗口内的连续跳变合并成一次；窗口取值只需盖住同一
 * 轮轮询写回引起的连锁跳变，不该长到让面板明显滞后于任务列表。
 */
const PLAN_REFRESH_DEBOUNCE_MS = 250;

const ASSET_ROUTES: [keyof ProjectData, string][] = [
  ["characters", WORKSPACE_ROUTE_CHARACTERS],
  ["scenes", WORKSPACE_ROUTE_SCENES],
  ["props", WORKSPACE_ROUTE_PROPS],
  ["products", WORKSPACE_ROUTE_PRODUCTS],
];

/** 资产名（衍生为「本体/衍生」）所在类型的画廊路由。 */
function assetRouteIn(project: ProjectData | null, name: string): string | null {
  if (!project) return null;
  const owner = name.split("/", 1)[0];
  for (const [bucket, route] of ASSET_ROUTES) {
    const table = project[bucket];
    if (table && typeof table === "object" && owner in table) return `/${route}`;
  }
  return null;
}

/**
 * 本集的剪辑概况，随计划刷新一起重取：剪辑时间线的写入与成片任务都会让计划刷新。
 * 计划里本集没有剪辑时间线时不请求。取不到时为 null，剪辑行退回只按计划陈述条数。
 */
function useEditOverview(projectName: string, episodeId: number | null, refetchOn: unknown, timelineCount: number) {
  const [overview, setOverview] = useState<{ key: string; value: EpisodeEditOverview } | null>(null);
  const key = `${projectName}::${episodeId ?? ""}`;
  useEffect(() => {
    if (episodeId == null || timelineCount === 0) return;
    const controller = new AbortController();
    API.getEpisodeEditOverview(projectName, episodeId, { signal: controller.signal })
      .then((value) => setOverview({ key, value }))
      .catch(() => {
        if (!controller.signal.aborted) setOverview(null);
      });
    return () => controller.abort();
  }, [projectName, episodeId, refetchOn, timelineCount, key]);
  return timelineCount > 0 && overview?.key === key ? overview.value : null;
}

const CLOSES_POPOVER = new Set<StepAct["intent"]["type"]>([
  "agent",
  "open_author_prompts",
  "open_script_plan",
  "open_ad_script",
  "open_script_plan_over_draft",
  "asset_batch",
  "storyboard_batch",
  "show_surface",
  "view_unit",
  "route",
]);

interface PendingDiscard {
  docType: DraftDocType;
  revision: string | null;
  agentOwned: boolean;
  fallbackText: string;
}

interface Props {
  projectName: string;
  episode: number | null;
  /** 跳到画布上的该单元。 */
  onViewUnit?: (unitId: string) => void;
  /** 显式重生某一步的指定单元。 */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  /** 打开范围为全部待编写的「编写提示词」；本集还没有正式脚本时不传。 */
  onAuthorPrompts?: () => void;
}

/**
 * 集页的制作进度（步骤清单），挂在集页页头第一行的行尾。
 *
 * 它投影后端给出的制作状态：每行一类内容的现状，建议的下一步就地展开在所属行，入口按
 * 「交给 Agent」为主、直接 AI 调用为次。界面不自行推断下一步；准入不满足的入口照常显示，
 * 悬停说明原因。过期、缺描述这类提醒只挂在对应行。
 *
 * 页头只放一枚入口：制作进度 · 一句话现状 · 阻断数；点开是非模态弹层，弹层是唯一的滚动区。
 * 计划的刷新也由它驱动，页头的批量补齐按钮读同一份计划。
 */
export function WorkflowPanel({ projectName, episode, onViewUnit, onRegenerate, onAuthorPrompts }: Props) {
  const { t, i18n } = useTranslation(["workflow", "dashboard", "assets"]);
  const alertId = useId();
  const [, setLocation] = useLocation();
  const [open, setOpen] = useState(false);
  const [assetBatchEpisode, setAssetBatchEpisode] = useState<number | null>(null);
  const [storyboardBatch, setStoryboardBatch] = useState<{ episodeId: number; kind: StoryboardBatchKind } | null>(
    null,
  );
  const [instructionDraft, setInstructionDraft] = useState<{ key: string; value: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [pendingDiscard, setPendingDiscard] = useState<PendingDiscard | null>(null);
  const [discarding, setDiscarding] = useState(false);

  const plan = useWorkflowStore((s) => s.plan);
  const planKey = useWorkflowStore((s) => s.planKey);
  const loading = useWorkflowStore((s) => s.loading);
  const error = useWorkflowStore((s) => s.error);
  const confirmDurations = useWorkflowStore((s) => s.confirmDurations);
  const confirmedDurations = useWorkflowStore((s) => s.confirmedDurations);
  const refreshPlan = useWorkflowStore((s) => s.refreshPlan);
  const resetTarget = useWorkflowStore((s) => s.resetTarget);
  const projectData = useProjectsStore((s) => s.currentProjectData);
  const ledger = useEpisodeLedger();

  // 项目快照修订号与任务指纹是两条既有的变更信号：前者随 project.json / 剧本写入递增
  // （SSE 项目事件驱动），后者随任务轮询变化。计划同时依赖这两类事实，故两者都进依赖。
  //
  // 任务指纹只取当前项目的任务：任务列表在「不按项目过滤」的作用域下是全局的，别的项目
  // 跑生成同样会让指纹变，本项目的计划却不会因此改变——不过滤就等于替别人的进度重求解。
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const taskFingerprint = useTasksStore((s) =>
    s.tasks
      .filter((task) => task.project_name === projectName)
      .map((task) => `${task.task_id}:${task.status}`)
      .join("|"),
  );
  const settledTaskFingerprint = useDebouncedValue(taskFingerprint, PLAN_REFRESH_DEBOUNCE_MS);

  useEffect(() => {
    if (!projectName) return;
    void refreshPlan(projectName, episode);
  }, [
    projectName,
    episode,
    snapshotRevision,
    settledTaskFingerprint,
    confirmedDurations,
    refreshPlan,
  ]);

  useEffect(() => () => resetTarget(), [resetTarget]);

  const currentKey = `${projectName}::${episode ?? ""}`;
  // 计划属于另一个目标时不拿它陈述当前目标——切集途中的旧事实比没有事实更糟。
  const shown = planKey === currentKey ? plan : null;
  const episodeId = shown?.status.target?.episode ?? episode;
  const episodeMeta = projectData?.episodes?.find((entry) => entry.episode === episodeId);
  const episodeRef = episodeId != null ? episodeAgentRef(ledger, episodeId, t) : "";
  const timelineIds = shown?.status.artifacts.edit_timelines?.timeline_ids;
  const editOverview = useEditOverview(projectName, episodeId ?? null, shown, Array.isArray(timelineIds) ? timelineIds.length : 0);

  const view = useMemo(() => {
    if (!shown || episodeId == null) return null;
    return buildStepList(shown, {
      t,
      lang: i18n.language,
      episodeId,
      episodeRef,
      episodeDurationSeconds: episodeMeta?.duration_seconds ?? null,
      adTargetSeconds: projectData?.content_mode === "ad" ? (projectData.target_duration ?? null) : null,
      assetRoute: (name) => assetRouteIn(projectData, name),
      savedPromptInstructions: episodeMeta?.prompt_authoring_instructions ?? "",
      canAuthorPrompts: Boolean(onAuthorPrompts),
      canViewUnit: Boolean(onViewUnit),
      editOverview,
    });
  }, [shown, episodeId, episodeRef, episodeMeta, projectData, t, i18n.language, onAuthorPrompts, onViewUnit, editOverview]);

  const blockers = useMemo(() => (shown ? blockerViews(t, shown.blockers) : []), [shown, t]);
  const issues = useMemo(() => (shown ? blockerViews(t, shown.status.issues) : []), [shown, t]);

  const next = view?.next ?? null;
  const instructionKey = next?.instruction ? `${currentKey}::${next.actionType}` : null;
  const instruction =
    instructionKey && instructionDraft?.key === instructionKey
      ? instructionDraft.value
      : (next?.instruction?.initial ?? "");

  const pushToast = useAppStore((s) => s.pushToast);

  const withInstruction = useCallback(
    (text: string) => {
      const extra = instruction.trim();
      return extra ? `${text}\n${t("workflow:agent_prefill_instructions", { instructions: extra })}` : text;
    },
    [instruction, t],
  );

  const run = useCallback(
    async (act: StepAct) => {
      if (episodeId == null) return;
      const intent = act.intent;
      // 交给 Agent、打开对话框或跳走的入口收起弹层，焦点和视线都在别处；就地执行的入口留着弹层看结果。
      if (CLOSES_POPOVER.has(intent.type)) setOpen(false);
      switch (intent.type) {
        case "agent":
          prefillAssistant(withInstruction(intent.text));
          return;
        case "open_author_prompts":
          onAuthorPrompts?.();
          return;
        case "open_script_plan":
          useScriptPlanStore.getState().open({ projectName, episode: episodeId, replaces: "formal_script" });
          return;
        case "open_ad_script":
          useAdScriptStore.getState().open({ projectName, episode: episodeId, regenerate: intent.regenerate });
          return;
        case "open_script_plan_over_draft":
          useScriptPlanStore.getState().open({ projectName, episode: episodeId, replaces: "draft" });
          return;
        case "asset_batch":
          setAssetBatchEpisode(intent.episodeId);
          return;
        case "storyboard_batch":
          setStoryboardBatch({ episodeId: intent.episodeId, kind: intent.kind });
          return;
        case "show_surface":
          useEpisodeSurfaceStore.getState().show({ projectName, episode: episodeId, surface: intent.surface });
          return;
        case "view_unit":
          onViewUnit?.(intent.unitId);
          return;
        case "route":
          setLocation(intent.path);
          return;
        default:
          break;
      }
      setRunning(true);
      try {
        switch (intent.type) {
          case "author_prompts_to_agent": {
            await API.savePromptAuthoringInstructions(projectName, episodeId, instruction.trim());
            const pending = shown?.status.content?.pending_authoring_ids.length ?? 0;
            prefillAssistant(
              promptAuthoringHandoffText(t, {
                episodeRef,
                scopeLabel: t("dashboard:prompt_authoring_scope_pending_prefill", { count: pending }),
                rewrite: false,
                instructions: instruction,
              }),
            );
            break;
          }
          case "author_prompts":
            if (isResourceBusy("text_episode_script", projectName, promptAuthoringResourceId(episodeId))) {
              pushToast(t("dashboard:prompt_authoring_busy"), "error");
              break;
            }
            await enqueuePromptAuthoring(projectName, episodeId, {
              entry_ids: null,
              rewrite: false,
              instructions: instruction.trim() || null,
              overwrite_revision: null,
            });
            break;
          case "generate_ad_script":
            if (isResourceBusy("text_episode_script", projectName, promptAuthoringResourceId(episodeId))) {
              pushToast(t("dashboard:ad_script_busy"), "error");
              break;
            }
            await enqueueAdScript(projectName, episodeId, {
              instructions: instruction.trim() || null,
              regenerate: false,
              overwrite_revision: null,
            });
            break;
          case "create_edit_timeline": {
            const created = await createScriptEditTimeline(projectName, episodeId, t);
            pushToast(t("workflow:edit_timeline_created", { name: created.timeline.name }), "success");
            void refreshPlan(projectName, episode);
            break;
          }
          case "start_blank_script":
            await API.startBlankScript(projectName, episodeId);
            await useProjectsStore.getState().refreshProject(projectName);
            void refreshPlan(projectName, episode);
            break;
          case "draft_to_agent": {
            const draft = await API.getEpisodeDraft(projectName, episodeId, intent.docType);
            prefillAssistant(withInstruction(draftFixRequestText(t, episodeRef, intent.docType, draft.violations)));
            break;
          }
          case "repair_draft": {
            if (isResourceBusy("text_draft_repair", projectName, draftRepairResourceId(episodeId, intent.docType))) {
              pushToast(t("dashboard:draft_repair_busy"), "error");
              break;
            }
            const draft = await API.getEpisodeDraft(projectName, episodeId, intent.docType);
            await enqueueDraftRepair(projectName, episodeId, intent.docType, draft.revision ?? "", instruction.trim() || null);
            break;
          }
          case "discard_draft": {
            const draft = await API.getEpisodeDraft(projectName, episodeId, intent.docType);
            setPendingDiscard({
              docType: intent.docType,
              revision: draft.revision,
              agentOwned: draft.editable_by === "agent",
              fallbackText: draftFallbackText(t, intent.docType, draft.formal_exists),
            });
            break;
          }
          default:
            break;
        }
      } catch (err) {
        pushToast(errMsg(err), "error");
      } finally {
        setRunning(false);
      }
    },
    [episodeId, episode, withInstruction, onAuthorPrompts, projectName, onViewUnit, setLocation, instruction, shown, t, episodeRef, pushToast, refreshPlan],
  );

  const viewUnit = useMemo(
    () =>
      onViewUnit
        ? (unitId: string) => {
            setOpen(false);
            onViewUnit(unitId);
          }
        : undefined,
    [onViewUnit],
  );

  const confirmDiscard = async () => {
    if (!pendingDiscard || episodeId == null) return;
    setDiscarding(true);
    try {
      await API.discardEpisodeDraft(projectName, episodeId, pendingDiscard.docType, pendingDiscard.revision);
      pushToast(t("dashboard:draft_discarded_toast"), "success");
    } catch (err) {
      pushToast(
        diagnosticCode(err) === "revision_conflict" ? t("dashboard:draft_conflict_toast") : errMsg(err),
        diagnosticCode(err) === "revision_conflict" ? "warning" : "error",
      );
    } finally {
      setDiscarding(false);
      setPendingDiscard(null);
      void refreshPlan(projectName, episode);
    }
  };

  // 计划没能投影成步骤清单（项目整体不可用、目标集绑定损坏）时，只复述后端给的下一步。
  const headline = view
    ? view.summary
    : shown
      ? nextStepForAction(t, shown.next_action.type)
      : loading
        ? t("plan_loading")
        : t("plan_unavailable");

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          data-testid="workflow-panel"
          render={<Button variant="outline" size="sm" className="max-w-[24em] min-w-0" />}
        >
          <ListChecks aria-hidden data-icon="inline-start" />
          <span className="shrink-0 font-medium text-foreground">{t("panel_title")}</span>
          <span className="min-w-0 truncate text-muted-foreground">{headline}</span>
          {blockers.length > 0 && (
            <Badge variant="destructive">
              <span aria-hidden>{blockers.length}</span>
              <span className="sr-only">{t("panel_blocker_count", { count: blockers.length })}</span>
            </Badge>
          )}
        </PopoverTrigger>
        {/* 非模态，贴着页头向下展开：不挤压画布，空间不够时收矮、弹层内部滚动，不翻到侧面盖住 Agent 面板。 */}
        <PopoverContent
          align="end"
          collisionAvoidance={{ side: "none" }}
          className="relative w-140 max-h-[min(70dvh,640px,var(--available-height))]"
        >
          <PopoverHeader>
            <PopoverTitle>{t("panel_title")}</PopoverTitle>
            <PopoverDescription>{headline}</PopoverDescription>
          </PopoverHeader>

          {view?.adDuration && (
            <Badge
              variant={view.adDuration.over ? "destructive" : "outline"}
              title={view.adDuration.over ? t("ad_duration_over_hint") : undefined}
              data-over={view.adDuration.over || undefined}
            >
              {view.adDuration.total != null
                ? t("ad_duration", { total: view.adDuration.total, target: view.adDuration.target })
                : t("ad_duration_no_script", { target: view.adDuration.target })}
            </Badge>
          )}

          {error && (
            <p className="text-xs text-muted-foreground" role="status">
              {t("plan_refresh_failed")}
            </p>
          )}

          {/* 顶部只陈述项目整体的阻断与内容的数据问题。项目级的计划提示（数据升级失败）与阻断是同一件事，
              不再列一遍；各步骤的提示与下一步只在所属行展开。 */}
          {blockers.length > 0 && (
            <Alert variant="destructive">
              <AlertTitle id={alertId}>{t("blockers_title", { count: blockers.length })}</AlertTitle>
              <AlertDescription>
                <ProblemList problems={blockers} labelledBy={alertId} className="mt-1" />
              </AlertDescription>
            </Alert>
          )}

          {issues.length > 0 && <ProblemList problems={issues} />}

          {view ? (
            <ol className="flex flex-col">
              {view.rows.map((row) => (
                <StepListRow
                  key={row.key}
                  row={row}
                  next={next?.rowKey === row.key ? next : null}
                  instruction={instruction}
                  onInstructionChange={(value) => instructionKey && setInstructionDraft({ key: instructionKey, value })}
                  onRun={(act) => void run(act)}
                  onViewUnit={viewUnit}
                  onRegenerate={onRegenerate}
                  onConfirmDurations={confirmDurations}
                  busy={loading || running}
                />
              ))}
            </ol>
          ) : (
            !shown && (
              <p className="text-xs text-muted-foreground">
                {loading ? t("plan_loading") : t("plan_unavailable")}
              </p>
            )
          )}
        </PopoverContent>
      </Popover>
      {assetBatchEpisode !== null && (
        <AssetSheetBatchDialog
          projectName={projectName}
          scope={{ episode_id: assetBatchEpisode }}
          onClose={() => setAssetBatchEpisode(null)}
        />
      )}
      {storyboardBatch !== null && (
        <StoryboardBatchDialog
          projectName={projectName}
          episode={storyboardBatch.episodeId}
          kind={storyboardBatch.kind}
          onClose={() => setStoryboardBatch(null)}
        />
      )}
      {pendingDiscard && (
        <DiscardDraftDialog
          open
          agentOwned={pendingDiscard.agentOwned}
          fallbackText={pendingDiscard.fallbackText}
          loading={discarding}
          onConfirm={() => void confirmDiscard()}
          onCancel={() => setPendingDiscard(null)}
        />
      )}
    </>
  );
}
