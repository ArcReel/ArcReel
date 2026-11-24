import type { TFunction } from "i18next";
import type { EpisodeEditOverview } from "@/types/edit-timeline";
import type { StoryboardBatchKind } from "@/types/storyboard-batch";
import type {
  WorkflowArtifactCollection,
  WorkflowContent,
  WorkflowNextAction,
  WorkflowOperation,
  WorkflowPlan,
  WorkflowPlanStep,
} from "@/types/workflow";
import { ROUTE_APP_SETTINGS, settingsSectionPath, WORKSPACE_ROUTE_PRODUCTS, episodeEditViewPath } from "@/app-routes";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import type { EpisodeSurface } from "@/stores/episode-surface-store";
import { formatNameList } from "@/utils/list-format";


export type StepRowKey =
  | "brief"
  | "source"
  | "plan"
  | "script"
  | "prompts"
  | "assets"
  | "boards"
  | "voice"
  | "videos"
  | "edit";

export type StepRowTone = "done" | "todo" | "partial" | "running" | "warn" | "danger";

export type StepIntent =
  | { type: "agent"; text: string }
  | { type: "author_prompts" }
  | { type: "author_prompts_to_agent" }
  | { type: "open_author_prompts" }
  | { type: "start_blank_script" }
  | { type: "open_script_plan" }
  | { type: "generate_ad_script" }
  | { type: "open_ad_script"; regenerate: boolean }
  | { type: "asset_batch"; episodeId: number }
  | { type: "storyboard_batch"; episodeId: number; kind: StoryboardBatchKind }
  | { type: "create_edit_timeline" }
  | { type: "show_surface"; surface: EpisodeSurface }
  | { type: "view_unit"; unitId: string }
  | { type: "route"; path: string };

export interface StepAct {
  key: string;
  label: string;
  kind: "agent" | "ai" | "nav" | "danger";
  intent: StepIntent;
  disabledReason?: string | null;
}

export interface StepNote {
  key: string;
  text: string;
  tone: "warn" | "danger" | "info";
  act?: StepAct;
}

export interface StepRowView {
  key: StepRowKey;
  title: string;
  tone: StepRowTone;
  status: string;
  notes: StepNote[];
  acts: StepAct[];
  steps: WorkflowPlanStep[];
}

export interface StepInstruction {
  initial: string;
  persist: "prompt_authoring" | null;
}

export interface NextStepView {
  rowKey: StepRowKey;
  actionType: WorkflowNextAction["type"];
  title: string;
  detail: string | null;
  instruction: StepInstruction | null;
  primary: StepAct[];
  alternatives: StepAct[];
  hint: StepNote | null;
}

export interface StepListView {
  rows: StepRowView[];
  next: NextStepView | null;
  summary: string;
  adDuration: { total: number | null; target: number; over: boolean } | null;
}

export interface StepListContext {
  t: TFunction;
  lang: string;
  episodeId: number;
  episodeRef: string;
  itemLabel: (itemId: string) => string;
  itemAgentRef: (itemId: string) => string;
  episodeDurationSeconds: number | null;
  adTargetSeconds: number | null;
  assetRoute: (name: string) => string | null;
  savedPromptInstructions: string;
  canAuthorPrompts: boolean;
  canViewUnit: boolean;
  editOverview: EpisodeEditOverview | null;
}

const AD_DURATION_OVER_RATIO = 1.1;

const ADMISSION_ACTIONS = new Set<WorkflowNextAction["type"]>([
  "retry",
  "fix_input",
  "generate_dependency",
  "replan_unit",
  "confirm_request_duration",
  "configure_provider",
  "configure_default_model",
  "repair_artifact_state",
  "retry_artifact_download",
]);

function ids(collection: WorkflowArtifactCollection | undefined, key: "current_ids" | "stale_ids" | "missing_ids") {
  const value = collection?.[key];
  return Array.isArray(value) ? value : [];
}

export function refusalReason(t: TFunction, operation: WorkflowOperation | undefined): string | null {
  if (!operation || operation.state === "admitted") return null;
  const reason = operation.reason ?? "unknown";
  return t(`workflow:refusal_${reason}`, { defaultValue: t("workflow:refusal_unknown") });
}

function operationApplies(operation: WorkflowOperation | undefined): boolean {
  return operation !== undefined && operation.state !== "not_applicable";
}

interface Facts {
  plan: WorkflowPlan;
  content: WorkflowContent;
  isAd: boolean;
  unitWordKey: "storyboard" | "unit";
  steps: Map<string, WorkflowPlanStep>;
}

function mediaStatus(t: TFunction, collection: WorkflowArtifactCollection | undefined): { status: string; tone: StepRowTone } {
  if (collection?.state === "blocked") return { status: t("workflow:status_unreadable"), tone: "danger" };
  const available = ids(collection, "current_ids").length + ids(collection, "stale_ids").length;
  const total = available + ids(collection, "missing_ids").length;
  if (total === 0) return { status: t("workflow:status_none"), tone: "todo" };
  if (available === total) return { status: t("workflow:status_media_all", { count: total }), tone: "done" };
  if (available === 0) return { status: t("workflow:status_media_none_of", { count: total }), tone: "todo" };
  return { status: t("workflow:status_media_some", { count: total, available }), tone: "partial" };
}

function provideSourceAct(t: TFunction): StepAct {
  return {
    key: "provide-source",
    label: t("workflow:act_provide_source"),
    kind: "nav",
    intent: { type: "show_surface", surface: "episode_source" },
  };
}

function goPlanScriptAct(t: TFunction): StepAct {
  return {
    key: "go-plan",
    label: t("workflow:act_go_plan_script"),
    kind: "nav",
    intent: { type: "show_surface", surface: "script_plan" },
  };
}

function blankScriptAct(t: TFunction): StepAct {
  return { key: "blank-script", label: t("dashboard:blank_script_start"), kind: "nav", intent: { type: "start_blank_script" } };
}

function stepsFor(facts: Facts, stepIds: string[]): WorkflowPlanStep[] {
  return stepIds.flatMap((id) => {
    const step = facts.steps.get(id);
    return step && step.state !== "skipped" ? [step] : [];
  });
}

function withRunning(row: StepRowView): StepRowView {
  return row.steps.some((step) => step.state === "active") ? { ...row, tone: "running" } : row;
}

function buildRows(facts: Facts, ctx: StepListContext): StepRowView[] {
  const { t } = ctx;
  const { plan, content } = facts;
  const status = plan.status;
  const formal = content.formal_script;
  const itemCount = content.script_item_count ?? 0;
  const rows: StepRowView[] = [];
  const nextType = plan.next_action.type;

  if (facts.isAd) {
    const present = content.ad_inputs === "present";
    const notes: StepNote[] = [];
    if (content.products_without_selling_points.length > 0) {
      notes.push({
        key: "selling-points",
        tone: "info",
        text: t("workflow:note_products_without_selling_points", {
          count: content.products_without_selling_points.length,
          names: formatNameList(content.products_without_selling_points, ctx.lang),
        }),
        act: {
          key: "selling-points",
          label: t("workflow:act_view_products"),
          kind: "nav",
          intent: { type: "route", path: `/${WORKSPACE_ROUTE_PRODUCTS}` },
        },
      });
    }
    rows.push({
      key: "brief",
      title: t("workflow:row_brief"),
      tone: present ? "done" : "todo",
      status: present ? t("workflow:status_brief_present") : t("workflow:status_brief_absent"),
      notes,
      acts: present
        ? [{ key: "edit-brief", label: t("workflow:act_edit_brief"), kind: "nav", intent: { type: "route", path: "/" } }]
        : [],
      steps: stepsFor(facts, ["project_input", "selling_points"]),
    });
  } else {
    const present = content.episode_source === "present";
    const notes: StepNote[] = [];
    if (content.episode_plan_stale) {
      notes.push({
        key: "plan-stale",
        tone: "warn",
        text: t("workflow:note_episode_plan_stale"),
        act: {
          key: "view-source",
          label: t("workflow:act_view_source"),
          kind: "nav",
          intent: { type: "route", path: episodesViewPath({ episode: ctx.episodeId }) },
        },
      });
    }
    const planState = status.artifacts.script_plan?.state;
    const sourceSurfaceShown = planState === undefined || planState === "missing";
    const acts: StepAct[] =
      !present &&
      formal !== "present" &&
      sourceSurfaceShown &&
      nextType !== "start_blank_script" &&
      nextType !== "provide_episode_source"
        ? [provideSourceAct(t)]
        : [];
    const planningSteps = stepsFor(facts, ["episode_plan"]);
    const planning = planningSteps.some((step) => step.state === "active");
    rows.push({
      key: "source",
      title: t("workflow:row_source"),
      tone: planning ? "running" : content.episode_plan_stale ? "warn" : present ? "done" : "todo",
      status: present ? t("workflow:status_source_present") : t("workflow:status_source_absent"),
      notes,
      acts,
      steps: planningSteps,
    });

    const review = status.gates.script_plan_review?.state;
    let planStatus: string;
    let planTone: StepRowTone;
    if (planState === "blocked") {
      planStatus = t("workflow:status_unreadable");
      planTone = "danger";
    } else if (planState === "current" || planState === "stale") {
      const confirmed = review === "confirmed";
      planStatus = confirmed ? t("workflow:status_plan_confirmed") : t("workflow:status_plan_pending_review");
      planTone = confirmed ? "done" : "partial";
    } else {
      planStatus = formal === "present" ? t("workflow:status_plan_not_used") : t("workflow:status_none");
      planTone = "todo";
    }
    const planOp = status.operations.prepare_script_plan;
    const planOffered = planState === "missing" && nextType !== "prepare_script_plan" && operationApplies(planOp);
    let planActs: StepAct[] = [];
    if (planOffered && formal !== "present") {
      planActs = [{ ...goPlanScriptAct(t), disabledReason: refusalReason(t, planOp) }];
    } else if (planOffered && formal === "present") {
      planActs = [
        {
          key: "plan",
          label: t("dashboard:script_plan_open"),
          kind: "ai",
          intent: { type: "open_script_plan" },
          disabledReason: refusalReason(t, planOp),
        },
      ];
    }
    rows.push({
      key: "plan",
      title: t("workflow:row_plan"),
      tone: planTone,
      status: planStatus,
      notes: [],
      acts: planActs,
      steps: stepsFor(facts, ["script_plan_content", "script_plan_review"]),
    });
  }

  const unitWord = facts.unitWordKey;
  let scriptStatus: string;
  let scriptTone: StepRowTone;
  if (formal === "invalid") {
    scriptStatus = t("workflow:status_unreadable");
    scriptTone = "danger";
  } else if (formal === "present") {
    const count = t(`workflow:status_items_${unitWord}`, { count: itemCount });
    scriptStatus =
      ctx.episodeDurationSeconds && itemCount > 0
        ? t("workflow:status_items_with_duration", { items: count, seconds: ctx.episodeDurationSeconds })
        : count;
    scriptTone = itemCount > 0 ? "done" : "todo";
  } else {
    scriptStatus = t("workflow:status_none");
    scriptTone = "todo";
  }
  const generateOp = status.operations.generate_script;
  const scriptActs: StepAct[] =
    facts.isAd && formal === "present" && operationApplies(generateOp)
      ? [
          {
            key: "regenerate-script",
            label: t("dashboard:ad_script_regenerate"),
            kind: "ai",
            intent: { type: "open_ad_script", regenerate: true },
            disabledReason: generateOp?.reason === "formal_script_exists" ? null : refusalReason(t, generateOp),
          },
        ]
      : [];
  rows.push({
    key: "script",
    title: t("workflow:row_script"),
    tone: scriptTone,
    status: scriptStatus,
    notes: [],
    acts: scriptActs,
    steps: stepsFor(facts, facts.isAd ? ["final_script", "script_structure"] : ["script_structure"]),
  });

  const pending = content.pending_authoring_ids.length;
  const replan = content.needs_replan_ids;
  let promptsStatus: string;
  let promptsTone: StepRowTone;
  if (formal !== "present" || itemCount === 0) {
    promptsStatus = t("workflow:status_none");
    promptsTone = "todo";
  } else if (pending > 0) {
    promptsStatus = t("workflow:status_prompts_pending", { count: pending, total: itemCount });
    promptsTone = "partial";
  } else {
    promptsStatus = t("workflow:status_prompts_done");
    promptsTone = "done";
  }
  const promptsNotes: StepNote[] = [];
  if (replan.length > 0 && nextType !== "repair_video_units") {
    promptsTone = "warn";
    promptsNotes.push({
      key: "replan",
      tone: "warn",
      text: t("workflow:note_needs_replan", { count: replan.length, ids: formatNameList(replan.map(ctx.itemLabel), ctx.lang) }),
      act: ctx.canViewUnit
        ? { key: "view-replan", label: t("workflow:act_view_unit", { id: ctx.itemLabel(replan[0]) }), kind: "nav", intent: { type: "view_unit", unitId: replan[0] } }
        : undefined,
    });
  }
  const authorOp = status.operations.author_prompts;
  const promptsActs: StepAct[] =
    pending > 0 && nextType !== "author_prompts" && ctx.canAuthorPrompts
      ? [
          {
            key: "author-prompts",
            label: t("dashboard:prompt_authoring_open"),
            kind: "ai",
            intent: { type: "open_author_prompts" },
            disabledReason: refusalReason(t, authorOp),
          },
        ]
      : [];
  rows.push({
    key: "prompts",
    title: t("workflow:row_prompts"),
    tone: promptsTone,
    status: promptsStatus,
    notes: promptsNotes,
    acts: promptsActs,
    steps: stepsFor(facts, facts.isAd ? [] : ["final_script"]),
  });

  rows.push(assetsRow(facts, ctx));

  if (status.project.generation_mode === "storyboard") {
    const media = mediaStatus(t, status.artifacts.storyboards);
    rows.push({
      key: "boards",
      title: status.project.grid_storyboard ? t("workflow:row_boards_grid") : t("workflow:row_boards"),
      tone: formal === "present" ? media.tone : "todo",
      status: formal === "present" ? media.status : t("workflow:status_none"),
      notes: [],
      acts: [],
      steps: stepsFor(facts, ["storyboard"]),
    });
  }

  if (status.project.content_mode === "narration" && status.project.generation_mode === "storyboard") {
    const audio = status.artifacts.audio;
    const postProduction = audio?.state === "not_applicable";
    const media = postProduction ? null : mediaStatus(t, audio);
    rows.push({
      key: "voice",
      title: t("workflow:row_voice"),
      tone: media && formal === "present" ? media.tone : postProduction ? "done" : "todo",
      status: postProduction
        ? t("workflow:status_voice_post_production")
        : formal === "present" && media
          ? media.status
          : t("workflow:status_none"),
      notes: [],
      acts: [],
      steps: [],
    });
  }

  const videos = mediaStatus(t, status.artifacts.videos);
  rows.push({
    key: "videos",
    title: t("workflow:row_videos"),
    tone: formal === "present" ? videos.tone : "todo",
    status: formal === "present" ? videos.status : t("workflow:status_none"),
    notes: [],
    acts: [],
    steps: stepsFor(facts, ["video"]),
  });

  rows.push(editRow(facts, ctx));

  return rows.map(withRunning);
}

function editActs(facts: Facts, ctx: StepListContext): StepAct[] {
  const { t } = ctx;
  const refusal = refusalReason(t, facts.plan.status.operations.create_edit_timeline);
  return [
    agentAct(t, t("workflow:agent_prefill_create_edit_timeline", { episodeRef: ctx.episodeRef }), t("workflow:act_agent_edit"), refusal),
    {
      key: "create-edit-timeline",
      label: t("workflow:act_create_edit_timeline"),
      kind: "ai",
      intent: { type: "create_edit_timeline" },
      disabledReason: refusal,
    },
  ];
}

function editTimelineCount(facts: Facts, ctx: StepListContext): number {
  const ids = facts.plan.status.artifacts.edit_timelines?.timeline_ids;
  return ctx.editOverview?.timeline_count ?? (Array.isArray(ids) ? ids.length : 0);
}

function openEditViewAct(ctx: StepListContext, key: string, label: string, timelineId?: string): StepAct {
  return { key, label, kind: "nav", intent: { type: "route", path: episodeEditViewPath(ctx.episodeId, timelineId) } };
}

function editRow(facts: Facts, ctx: StepListContext): StepRowView {
  const { t } = ctx;
  const { plan, content } = facts;
  const overview = ctx.editOverview;
  const count = editTimelineCount(facts, ctx);
  const issues = overview?.latest?.issue_count ?? 0;
  let status: string;
  if (count === 0) status = t("workflow:status_edit_none");
  else if (issues > 0) status = t("workflow:status_edit_timelines_with_issues", { count, issues });
  else status = t("workflow:status_edit_timelines", { count });
  const notes: StepNote[] = [];
  const stale = overview?.stale_final_cuts ?? [];
  if (stale.length > 0) {
    notes.push({
      key: "final-cut-stale",
      tone: "warn",
      text: t("workflow:note_final_cut_stale", {
        count: stale.length,
        names: formatNameList(stale.map((timeline) => timeline.name), ctx.lang),
      }),
      act: openEditViewAct(ctx, "final-cut-stale-render", t("workflow:act_go_render"), stale[0].id),
    });
  }
  const hasItems = content.formal_script === "present" && (content.script_item_count ?? 0) > 0;
  const acts = hasItems && plan.next_action.type !== "create_edit_timeline" ? editActs(facts, ctx) : [];
  if (count > 0) acts.push(openEditViewAct(ctx, "open-edit-view", t("workflow:act_open_edit_view")));
  return {
    key: "edit",
    title: t("workflow:row_edit"),
    tone: count > 0 ? "done" : "todo",
    status,
    notes,
    acts,
    steps: stepsFor(facts, ["edit"]),
  };
}

function assetsRow(facts: Facts, ctx: StepListContext): StepRowView {
  const { t } = ctx;
  const { content } = facts;
  const formal = content.formal_script === "present" && (content.script_item_count ?? 0) > 0;
  const withoutDescription = new Set(content.referenced_assets_without_description);
  const toGenerate = content.referenced_assets_without_sheet.filter((name) => !withoutDescription.has(name));
  const stale = content.referenced_asset_sheets_stale;
  const notes: StepNote[] = [];
  const galleryAct = (key: string, label: string, name: string | undefined): StepAct | undefined => {
    const path = name ? ctx.assetRoute(name) : null;
    return path ? { key, label, kind: "nav", intent: { type: "route", path } } : undefined;
  };
  if (stale.length > 0) {
    notes.push({
      key: "stale",
      tone: "warn",
      text: t("workflow:note_assets_stale", { count: stale.length, names: formatNameList(stale, ctx.lang) }),
      act: galleryAct("stale-gallery", t("workflow:act_view_gallery"), stale[0]),
    });
  }
  if (withoutDescription.size > 0) {
    const names = content.referenced_assets_without_description;
    notes.push({
      key: "no-description",
      tone: "info",
      text: t("workflow:note_assets_without_description", { count: names.length, names: formatNameList(names, ctx.lang) }),
      act: galleryAct("describe", t("workflow:act_fill_description"), names[0]),
    });
  }
  if (content.unregistered_references.length > 0) {
    const names = content.unregistered_references;
    notes.push({
      key: "unregistered",
      tone: "warn",
      text: t("workflow:note_unregistered_references", { count: names.length, names: formatNameList(names, ctx.lang) }),
    });
  }
  let status: string;
  let tone: StepRowTone;
  if (!formal) {
    status = t("workflow:status_none");
    tone = "todo";
  } else if (toGenerate.length > 0) {
    status = t("workflow:status_assets_missing", { count: toGenerate.length });
    tone = "partial";
  } else if (content.referenced_assets_without_sheet.length > 0) {
    status = t("workflow:status_assets_unsheeted", { count: content.referenced_assets_without_sheet.length });
    tone = "warn";
  } else {
    status = t("workflow:status_assets_done");
    tone = stale.length > 0 ? "warn" : "done";
  }
  return {
    key: "assets",
    title: t("workflow:row_assets"),
    tone,
    status,
    notes,
    acts: [],
    steps: stepsFor(facts, ["asset_sheets"]),
  };
}

function ownerRow(facts: Facts, rows: StepRowView[], action: WorkflowNextAction): StepRowKey | null {
  switch (action.type) {
    case "prepare_script_plan":
    case "confirm_script_plan":
      return "plan";
    case "provide_episode_source":
      return facts.isAd ? "script" : "source";
    case "collect_project_input":
      return facts.isAd ? "brief" : null;
    case "start_blank_script":
    case "generate_script":
    case "add_script_items":
    case "patch_episode_script":
      return "script";
    case "author_prompts":
    case "repair_video_units":
      return "prompts";
    case "generate_asset_sheets":
      return "assets";
    case "generate_storyboards":
    case "generate_grid":
      return rows.some((row) => row.key === "boards") ? "boards" : null;
    case "generate_videos":
      return "videos";
    case "create_edit_timeline":
      return "edit";
    case "wait_for_task":
      return rows.find((row) => row.steps.some((step) => step.state === "active"))?.key ?? null;
    default:
      return ADMISSION_ACTIONS.has(action.type) ? "videos" : null;
  }
}

function agentAct(t: TFunction, text: string, label?: string, disabledReason?: string | null): StepAct {
  return {
    key: "agent",
    label: label ?? t("workflow:act_agent"),
    kind: "agent",
    intent: { type: "agent", text },
    disabledReason,
  };
}

function adScriptAct(t: TFunction, disabledReason?: string | null): StepAct {
  return {
    key: "ai-generate-script",
    label: t("dashboard:ad_script_generate"),
    kind: "ai",
    intent: { type: "generate_ad_script" },
    disabledReason,
  };
}

function buildNext(facts: Facts, rows: StepRowView[], ctx: StepListContext): NextStepView | null {
  const { t } = ctx;
  const { plan, content } = facts;
  const action = plan.next_action;
  if (action.type === "none" || plan.blockers.length > 0) return null;
  const rowKey = ownerRow(facts, rows, action);
  if (rowKey === null) return null;
  const episodeRef = ctx.episodeRef;
  const count = action.requested_ids.length;
  const base = {
    rowKey,
    actionType: action.type,
    title: t(`workflow:next_title_${action.type}`, { defaultValue: t(`workflow:action_${action.type}`, { defaultValue: t("workflow:action_unknown") }) }),
    detail: null as string | null,
    instruction: null as StepInstruction | null,
    primary: [] as StepAct[],
    alternatives: [] as StepAct[],
    hint: null as StepNote | null,
  };
  const alternatives = plan.next_alternatives.flatMap((alt): StepAct[] => {
    if (alt.type === "provide_episode_source" && !facts.isAd) return [provideSourceAct(t)];
    if (alt.type === "start_blank_script") return [blankScriptAct(t)];
    if (alt.type === "create_episode") {
      return [
        {
          key: "create-episode",
          label: t("workflow:action_create_episode"),
          kind: "nav",
          intent: { type: "route", path: episodesViewPath({ create: true }) },
        },
      ];
    }
    return [];
  });

  switch (action.type) {
    case "prepare_script_plan":
      return {
        ...base,
        detail: t("workflow:next_detail_prepare_script_plan"),
        primary: [goPlanScriptAct(t)],
        alternatives,
      };
    case "start_blank_script":
      return {
        ...base,
        detail: t(`workflow:next_detail_start_blank_script_${facts.unitWordKey}`),
        primary: [blankScriptAct(t)],
        alternatives,
      };
    case "provide_episode_source":
      return {
        ...base,
        detail: t("workflow:next_detail_provide_episode_source"),
        primary: [provideSourceAct(t)],
      };
    case "confirm_script_plan":
      return {
        ...base,
        detail: t("workflow:next_detail_confirm_script_plan"),
        primary: [
          { key: "go-confirm", label: t("workflow:act_go_confirm"), kind: "nav", intent: { type: "show_surface", surface: "script_plan" } },
        ],
      };
    case "generate_script":
      return {
        ...base,
        detail: t("workflow:next_detail_generate_script"),
        instruction: { initial: "", persist: null },
        primary: [agentAct(t, t("workflow:agent_prefill_generate_script", { episodeRef })), adScriptAct(t)],
        alternatives,
      };
    case "collect_project_input": {
      const reason = refusalReason(t, plan.status.operations.generate_script) ?? t("workflow:refusal_ad_brief_and_products_missing");
      return {
        ...base,
        title: t("workflow:next_title_generate_script"),
        detail: t("workflow:next_detail_generate_script"),
        primary: [
          agentAct(t, t("workflow:agent_prefill_generate_script", { episodeRef }), undefined, reason),
          adScriptAct(t, reason),
        ],
        alternatives,
        hint: {
          key: "fill-brief",
          tone: "info",
          text: t("workflow:hint_fill_brief"),
          act: { key: "fill-brief", label: t("workflow:act_fill_brief"), kind: "nav", intent: { type: "route", path: "/" } },
        },
      };
    }
    case "add_script_items":
      return {
        ...base,
        title: t(`workflow:next_title_add_script_items_${facts.unitWordKey}`),
        detail: t(`workflow:next_detail_add_script_items_${facts.unitWordKey}`),
        primary: [agentAct(t, t(`workflow:agent_prefill_add_script_items_${facts.unitWordKey}`, { episodeRef }))],
        alternatives,
      };
    case "author_prompts": {
      const pending = content.pending_authoring_ids.length;
      const primary: StepAct[] = [
        { key: "agent", label: t("workflow:act_agent"), kind: "agent", intent: { type: "author_prompts_to_agent" } },
      ];
      if (ctx.canAuthorPrompts) {
        primary.push({ key: "author", label: t("workflow:act_author_prompts"), kind: "ai", intent: { type: "author_prompts" } });
      }
      return {
        ...base,
        detail: t("workflow:next_detail_author_prompts", { count: pending }),
        instruction: { initial: ctx.savedPromptInstructions, persist: "prompt_authoring" },
        primary,
      };
    }
    case "repair_video_units": {
      const first = action.requested_ids[0];
      const primary: StepAct[] = [
        agentAct(
          t,
          t("workflow:agent_prefill_repair_video_units", {
            episodeRef,
            ids: formatNameList(action.requested_ids.map(ctx.itemAgentRef), ctx.lang),
          }),
        ),
      ];
      if (first && ctx.canViewUnit) {
        primary.push({ key: "view", label: t("workflow:act_view_unit", { id: ctx.itemLabel(first) }), kind: "nav", intent: { type: "view_unit", unitId: first } });
      }
      return {
        ...base,
        detail: t("workflow:next_detail_repair_video_units", {
          count,
          ids: formatNameList(action.requested_ids.map(ctx.itemLabel), ctx.lang),
        }),
        primary,
      };
    }
    case "patch_episode_script":
      return {
        ...base,
        detail: t("workflow:next_detail_patch_episode_script"),
        primary: [agentAct(t, t("workflow:agent_prefill_patch_episode_script", { episodeRef }))],
      };
    case "generate_asset_sheets":
      return {
        ...base,
        detail: t("workflow:next_detail_generate_asset_sheets", { count, names: formatNameList(action.requested_ids, ctx.lang) }),
        primary: [
          agentAct(t, t("workflow:agent_prefill_generate_asset_sheets", { episodeRef })),
          {
            key: "batch",
            label: t("workflow:act_asset_batch", { count }),
            kind: "ai",
            intent: { type: "asset_batch", episodeId: ctx.episodeId },
          },
        ],
      };
    case "generate_storyboards":
    case "generate_grid":
    case "generate_videos": {
      const primary = [agentAct(t, t(`workflow:agent_prefill_${action.type}`, { episodeRef }))];
      const batchKind: StoryboardBatchKind | null =
        facts.plan.status.project.generation_mode === "reference_video"
          ? null
          : action.type === "generate_storyboards"
            ? "storyboards"
            : action.type === "generate_videos"
              ? "videos"
              : null;
      if (batchKind) {
        primary.push({
          key: "batch",
          label: t(batchKind === "storyboards" ? "dashboard:batch_generate_storyboards" : "dashboard:batch_generate_videos"),
          kind: "ai",
          intent: { type: "storyboard_batch", episodeId: ctx.episodeId, kind: batchKind },
        });
      }
      return {
        ...base,
        detail: t(`workflow:next_detail_${action.type}`, { count }),
        primary,
        hint: unsheetedAssetsHint(facts, ctx, action.type),
      };
    }
    case "create_edit_timeline":
      return {
        ...base,
        detail: t("workflow:next_detail_create_edit_timeline"),
        instruction: { initial: "", persist: null },
        primary: editActs(facts, ctx),
        alternatives:
          editTimelineCount(facts, ctx) > 0
            ? [openEditViewAct(ctx, "open-edit-view", t("workflow:act_open_edit_view"))]
            : [],
      };
    case "wait_for_task":
      if (rowKey === "source") {
        return {
          ...base,
          title: t("workflow:next_title_wait_for_episode_planning"),
          detail: t("workflow:next_detail_wait_for_episode_planning"),
          primary: [
            {
              key: "view-planning",
              label: t("workflow:act_view_planning_progress"),
              kind: "nav",
              intent: { type: "route", path: episodesViewPath() },
            },
          ],
        };
      }
      return { ...base, detail: t("workflow:next_detail_wait_for_task") };
    case "configure_default_model":
      return {
        ...base,
        primary: [
          { key: "settings", label: t("workflow:act_configure_default_model"), kind: "nav", intent: { type: "route", path: `~${settingsSectionPath("default-models")}` } },
        ],
      };
    case "configure_provider":
      return {
        ...base,
        primary: [
          { key: "settings", label: t("workflow:act_open_settings"), kind: "nav", intent: { type: "route", path: `~${ROUTE_APP_SETTINGS}` } },
        ],
      };
    case "confirm_request_duration":
      return base;
    default:
      return ADMISSION_ACTIONS.has(action.type)
        ? { ...base, primary: [agentAct(t, t("workflow:agent_prefill_admission", { episodeRef }))] }
        : base;
  }
}

function unsheetedAssetsHint(facts: Facts, ctx: StepListContext, actionType: WorkflowNextAction["type"]): StepNote | null {
  const names = facts.content.referenced_assets_without_sheet;
  if (names.length === 0) return null;
  if (actionType === "generate_videos" && facts.plan.status.project.generation_mode !== "reference_video") return null;
  const { t } = ctx;
  const path = ctx.assetRoute(names[0]);
  return {
    key: "unsheeted-assets",
    tone: "warn",
    text: t(`workflow:hint_assets_without_sheet_${facts.unitWordKey}`, { count: names.length }),
    act: path ? { key: "unsheeted-gallery", label: t("workflow:act_view_gallery"), kind: "nav", intent: { type: "route", path } } : undefined,
  };
}

function adDuration(facts: Facts, ctx: StepListContext): StepListView["adDuration"] {
  if (!facts.isAd || !ctx.adTargetSeconds) return null;
  const total = facts.content.formal_script === "present" ? ctx.episodeDurationSeconds : null;
  return {
    total,
    target: ctx.adTargetSeconds,
    over: total !== null && total > ctx.adTargetSeconds * AD_DURATION_OVER_RATIO,
  };
}

export function buildStepList(plan: WorkflowPlan, ctx: StepListContext): StepListView | null {
  const content = plan.status.content;
  if (content === null) return null;
  const facts: Facts = {
    plan,
    content,
    isAd: plan.status.project.content_mode === "ad",
    unitWordKey: plan.status.project.generation_mode === "reference_video" ? "unit" : "storyboard",
    steps: new Map(plan.steps.map((step) => [step.id, step])),
  };
  const rows = buildRows(facts, ctx);
  const next = buildNext(facts, rows, ctx);
  const { t } = ctx;
  let summary: string;
  const editDone = content.episode_complete === true;
  if (next) {
    const row = rows.find((candidate) => candidate.key === next.rowKey);
    summary = row ? t("workflow:summary_row", { title: row.title, status: row.status }) : next.title;
  } else if (editDone) {
    summary = t("workflow:summary_complete");
  } else {
    const flagged = rows.find((row) => row.tone === "danger" || row.tone === "warn");
    summary = flagged ? t("workflow:summary_row", { title: flagged.title, status: flagged.status }) : "";
  }
  return { rows, next, summary, adDuration: adDuration(facts, ctx) };
}
