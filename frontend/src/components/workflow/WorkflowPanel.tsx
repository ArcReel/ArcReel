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
import { refreshAfterWrite } from "@/components/canvas/shared/refreshAfterWrite";
import { promptAuthoringHandoffText } from "@/components/canvas/shared/prompt-authoring-handoff";
import { DiscardDraftDialog } from "@/components/shared/DraftStatus";
import { draftFallbackText, draftFixRequestText } from "@/components/shared/draft-text";
import { prefillAssistant } from "@/components/shared/prefill-assistant";
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
import { episodeAgentRef, episodeItemAgentRef, itemPositionLabel } from "@/utils/episode-display";
import { episodeItemKind, findEpisodeItem } from "@/utils/episode-item";
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
 * Merge window from changed task fingerprints to new solves, in milliseconds.
 *
 * Batch enqueue/start/completion changes states one row at a time. Solving every change would send
 * over a dozen `POST /workflow-plan` for one batch; merge consecutive window changes once. Cover same-poll
 * write cascades only, never long enough to noticeably lag task lists.
 */
const PLAN_REFRESH_DEBOUNCE_MS = 250;

const ASSET_ROUTES: [keyof ProjectData, string][] = [
  ["characters", WORKSPACE_ROUTE_CHARACTERS],
  ["scenes", WORKSPACE_ROUTE_SCENES],
  ["props", WORKSPACE_ROUTE_PROPS],
  ["products", WORKSPACE_ROUTE_PRODUCTS],
];

/** Gallery routes for asset types, with derivative names in base/derivative form. */
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
 * Episode editing overviews refetch with plans; timeline writes/finished-video tasks trigger plan refreshes.
 * Skip requests without episode editing timelines in plans. Null on unavailable overviews; editing rows report plan counts only.
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
  /** Navigate to canvas units. */
  onViewUnit?: (unitId: string) => void;
  /** Explicitly regenerate specified units within steps. */
  onRegenerate?: (stepId: string, unitIds: string[]) => void;
  /** Open Write prompts scoped to all entries pending authoring; omit without formal episode scripts. */
  onAuthorPrompts?: () => void;
}

/**
 * Episode production progress (step lists), at ends of first episode-header rows.
 *
 * Project backend production states: each row describes one content class, expanding suggested next steps within owning rows;
 * Hand to Agent primary, direct AI secondary. UIs never infer next steps; inadmissible entries remain visible
 * with hover reasons. Stale/missing-description reminders attach only to relevant rows.
 *
 * Headers contain one entry: Production progress ·status sentence ·blocking count; opening shows nonmodal popovers as sole scroll regions.
 * Panels drive plan refreshes; header batch-fill buttons read the same plans.
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
  const scripts = useProjectsStore((s) => s.currentScripts);
  const ledger = useEpisodeLedger();

  // Existing change signals: project snapshot revisions rise with project.json/script writes
  // driven by SSE events; task fingerprints change through polling. Plans depend on both fact sources, so depend on both.
  //
  // Task fingerprints use current-project tasks only; task lists in unfiltered scopes are global, so other-project
  // generations change fingerprints without changing current-project plans, otherwise needlessly resolving others’ progress.
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
  // Plans targeting other episodes must not describe current targets; stale switch-time facts are worse than none.
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
      itemLabel: (itemId) =>
        itemPositionLabel(episodeItemKind(itemId), findEpisodeItem(scripts, itemId)?.position ?? null, t),
      itemAgentRef: (itemId) => episodeItemAgentRef(itemId, scripts, t),
      episodeDurationSeconds: episodeMeta?.duration_seconds ?? null,
      adTargetSeconds: projectData?.content_mode === "ad" ? (projectData.target_duration ?? null) : null,
      assetRoute: (name) => assetRouteIn(projectData, name),
      savedPromptInstructions: episodeMeta?.prompt_authoring_instructions ?? "",
      canAuthorPrompts: Boolean(onAuthorPrompts),
      canViewUnit: Boolean(onViewUnit),
      editOverview,
    });
  }, [shown, episodeId, episodeRef, scripts, episodeMeta, projectData, t, i18n.language, onAuthorPrompts, onViewUnit, editOverview]);

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
      // Hand to Agent/dialog/navigation entries close popovers because focus/attention moves; inline execution keeps results visible.
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
            const { created, refreshed } = await createScriptEditTimeline(projectName, episodeId, t);
            // Refresh failures already notify; never simultaneously report success.
            if (refreshed === "success") {
              pushToast(t("workflow:edit_timeline_created", { name: created.timeline.name }), "success");
            }
            void refreshPlan(projectName, episode);
            break;
          }
          case "start_blank_script":
            await API.startBlankScript(projectName, episodeId);
            await refreshAfterWrite(projectName, t);
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

  // Plans unable to form step lists (unusable projects/broken episode bindings) repeat backend next steps only.
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
        {/* Nonmodal beneath headers without squeezing canvases; insufficient room lowers height/internal scrolling instead of flipping sideways over Agent panels. */}
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

          {/* Top regions state only project-wide blockers/content-data issues. Project plan notices (upgrade failures) duplicate blockers,
              so omit repetitions; step notices/next steps expand only within owning rows. */}
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
