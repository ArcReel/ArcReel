import type { TFunction } from "i18next";
import type { EpisodeEditOverview } from "@/types/edit-timeline";
import type { DraftDocType } from "@/types/reference-video";
import type { StoryboardBatchKind } from "@/types/storyboard-batch";
import type {
  WorkflowArtifactCollection,
  WorkflowContent,
  WorkflowDraft,
  WorkflowNextAction,
  WorkflowOperation,
  WorkflowPlan,
  WorkflowPlanStep,
} from "@/types/workflow";
import { ROUTE_APP_SETTINGS, WORKSPACE_ROUTE_PRODUCTS, episodeEditViewPath } from "@/app-routes";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import type { EpisodeSurface } from "@/stores/episode-surface-store";
import { formatNameList } from "@/utils/list-format";

/**
 * 制作进度面板的「步骤清单」投影：把后端的制作状态（内容现状、各操作准入、建议的下一步）
 * 排成一行一类内容，下一步挂在它所属的那一行上。
 *
 * 每行只陈述自己那类内容此刻的样子，不看前面的步骤——前一步没做完，后面一行照样写「还没有」，
 * 不写「等待上一步」。过期、缺描述这类提醒挂在对应行上，不进下一步。
 */

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

/** 行的现状色调：已齐 / 还没有 / 部分 / 进行中 / 需要留意 / 读不出。 */
export type StepRowTone = "done" | "todo" | "partial" | "running" | "warn" | "danger";

/** 按下一个入口之后要做的事；面板按类型分派到具体的处理函数。 */
export type StepIntent =
  | { type: "agent"; text: string }
  | { type: "draft_to_agent"; docType: DraftDocType }
  | { type: "repair_draft"; docType: DraftDocType }
  | { type: "author_prompts" }
  | { type: "author_prompts_to_agent" }
  | { type: "open_author_prompts" }
  | { type: "plan_script" }
  | { type: "plan_script_to_agent" }
  | { type: "start_blank_script" }
  | { type: "open_script_plan" }
  | { type: "generate_ad_script" }
  | { type: "open_ad_script"; regenerate: boolean }
  | { type: "open_script_plan_over_draft" }
  | { type: "asset_batch"; episodeId: number }
  | { type: "storyboard_batch"; episodeId: number; kind: StoryboardBatchKind }
  | { type: "create_edit_timeline" }
  | { type: "discard_draft"; docType: DraftDocType }
  | { type: "show_surface"; surface: EpisodeSurface }
  | { type: "view_unit"; unitId: string }
  | { type: "route"; path: string };

/**
 * 一个入口。`agent` 是「交给 Agent」（实心），`ai` 是直接 AI 调用（描边），`nav` 是跳转或手动去做（文字链），
 * `danger` 是需确认的破坏性操作（红字链）。`disabledReason` 非空时入口照常显示但不可点，悬停说明原因。
 */
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
  /** 本行常驻的入口（不是建议的下一步）。 */
  acts: StepAct[];
  /** 归到这一行的后端步骤：任务、结构问题、过期产物与整批准入从这里取。 */
  steps: WorkflowPlanStep[];
}

/** 附加指令：`persist` 非空时按集保存到对应字段，为 null 时只随本次调用带上。 */
export interface StepInstruction {
  initial: string;
  persist: "prompt_authoring" | "script_plan" | null;
}

export interface NextStepView {
  rowKey: StepRowKey;
  /** 下一步的动作类型，面板用它区分附加指令的归属。 */
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
  /** 收起时的一句话现状。 */
  summary: string;
  /** 广告/短片标题行的「总时长 / 目标时长」；`over` 为超出目标 10% 以上。 */
  adDuration: { total: number | null; target: number; over: boolean } | null;
}

export interface StepListContext {
  t: TFunction;
  /** 界面语言，名单按它连接。 */
  lang: string;
  episodeId: number;
  /** 集名连同集 ID 的指称，预填给 Agent 用（`episodeAgentRef`）。 */
  episodeRef: string;
  /** 本集正式脚本的总时长（秒），项目摘要给出；未知时为 null。 */
  episodeDurationSeconds: number | null;
  /** 广告/短片的目标时长（秒）；未设或非广告为 null。 */
  adTargetSeconds: number | null;
  /** 资产名对应的画廊路由；名字不在任何资产表里时为 null。 */
  assetRoute: (name: string) => string | null;
  /** 本集保存的提示词编写附加指令。 */
  savedPromptInstructions: string;
  /** 本集保存的 AI 规划脚本附加指令。 */
  savedScriptPlanInstructions: string;
  /** 集页上是否挂着「编写提示词」的宿主（有正式脚本时才有）。 */
  canAuthorPrompts: boolean;
  /** 能否跳到画布上的某个单元。 */
  canViewUnit: boolean;
  /** 本集的剪辑概况；还没取到或本集没有剪辑时间线时为 null。 */
  editOverview: EpisodeEditOverview | null;
}

/** 状态里的草稿种类 → 草稿端点的 `doc_type`。 */
const DOC_TYPE_BY_DRAFT_KIND: Record<string, DraftDocType> = {
  drama_script_plan: "drama_script_plan",
  narration_script_plan: "narration_script_plan",
  reference_video_script_plan: "reference_script_plan",
  reference_video_prompt_authoring: "reference_prompt_authoring",
};

const PROMPT_AUTHORING_DRAFT_KIND = "reference_video_prompt_authoring";

/** 超出目标时长多少算「明显超出」，按比例。 */
const AD_DURATION_OVER_RATIO = 1.1;

/** 整批准入与任务失败交回的动作：它们的入口在视频行的准入陈述里，不在下一步里另起按钮。 */
const ADMISSION_ACTIONS = new Set<WorkflowNextAction["type"]>([
  "retry",
  "fix_input",
  "generate_dependency",
  "replan_unit",
  "confirm_request_duration",
  "configure_provider",
  "repair_artifact_state",
  "retry_artifact_download",
]);

export function draftDocType(draft: WorkflowDraft): DraftDocType | null {
  return DOC_TYPE_BY_DRAFT_KIND[draft.kind] ?? null;
}

function ids(collection: WorkflowArtifactCollection | undefined, key: "current_ids" | "stale_ids" | "missing_ids") {
  const value = collection?.[key];
  return Array.isArray(value) ? value : [];
}

/** 准入被拒时悬停显示的原因，准入成立时为 null。「此类项目不适用」的操作由调用方用 `operationApplies` 过滤掉入口。 */
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

/** 媒体类内容的一句话现状：「N 个中 M 个已生成」，都齐时「全部 N 个已生成」。 */
function mediaStatus(t: TFunction, collection: WorkflowArtifactCollection | undefined): { status: string; tone: StepRowTone } {
  if (collection?.state === "blocked") return { status: t("workflow:status_unreadable"), tone: "danger" };
  const available = ids(collection, "current_ids").length + ids(collection, "stale_ids").length;
  const total = available + ids(collection, "missing_ids").length;
  if (total === 0) return { status: t("workflow:status_none"), tone: "todo" };
  if (available === total) return { status: t("workflow:status_media_all", { count: total }), tone: "done" };
  if (available === 0) return { status: t("workflow:status_media_none_of", { count: total }), tone: "todo" };
  return { status: t("workflow:status_media_some", { count: total, available }), tone: "partial" };
}

/** 补充集原文：集页上无原文的集直接显示填写框，按下后聚焦到它。 */
function provideSourceAct(t: TFunction): StepAct {
  return {
    key: "provide-source",
    label: t("workflow:act_provide_source"),
    kind: "nav",
    intent: { type: "show_surface", surface: "episode_source" },
  };
}

function blankScriptAct(t: TFunction): StepAct {
  return { key: "blank-script", label: t("dashboard:blank_script_start"), kind: "nav", intent: { type: "start_blank_script" } };
}

function draftStatus(t: TFunction, draft: WorkflowDraft): string {
  return draft.needs_repair ? t("workflow:status_draft_repair") : t("workflow:status_draft_agent");
}

function draftOf(content: WorkflowContent, promptAuthoring: boolean): WorkflowDraft | undefined {
  return content.drafts.find((draft) => (draft.kind === PROMPT_AUTHORING_DRAFT_KIND) === promptAuthoring);
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
    const planDraft = draftOf(content, false);
    const planState = status.artifacts.script_plan?.state;
    // 填写集原文的界面只在本集既没有规划也没有草稿时显示，其余时候这个入口点了没有去处。
    const sourceSurfaceShown = (planState === undefined || planState === "missing") && content.drafts.length === 0;
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
    if (planDraft) {
      planStatus = draftStatus(t, planDraft);
      planTone = "warn";
    } else if (planState === "blocked") {
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
    const planOffered = !planDraft && planState === "missing" && nextType !== "prepare_script_plan" && operationApplies(planOp);
    let planActs: StepAct[] = [];
    if (planOffered && formal !== "present") {
      planActs = [
        {
          key: "agent-plan",
          label: t("workflow:act_agent_plan_script"),
          kind: "agent",
          intent: { type: "agent", text: t("dashboard:script_plan_agent_prefill", { episodeRef: ctx.episodeRef }) },
          disabledReason: refusalReason(t, planOp),
        },
      ];
    } else if (planOffered && formal === "present") {
      // 已有正式脚本（如从空白开始）时也能整集交给 AI 规划；新规划待确认，经覆盖确认才替换正式脚本。
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
  // 广告/短片的整份重做：服务端先报输入缺失，`formal_script_exists` 只拦首次生成，不拦重做。
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

  const promptDraft = draftOf(content, true);
  const pending = content.pending_authoring_ids.length;
  const replan = content.needs_replan_ids;
  let promptsStatus: string;
  let promptsTone: StepRowTone;
  if (promptDraft) {
    promptsStatus = draftStatus(t, promptDraft);
    promptsTone = "warn";
  } else if (formal !== "present" || itemCount === 0) {
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
      text: t("workflow:note_needs_replan", { count: replan.length, ids: formatNameList(replan, ctx.lang) }),
      act: ctx.canViewUnit
        ? { key: "view-replan", label: t("workflow:act_view_unit", { id: replan[0] }), kind: "nav", intent: { type: "view_unit", unitId: replan[0] } }
        : undefined,
    });
  }
  const authorOp = status.operations.author_prompts;
  const promptsActs: StepAct[] =
    pending > 0 && nextType !== "author_prompts" && !promptDraft && ctx.canAuthorPrompts
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

/** 剪辑的两个入口：交给 Agent 剪辑为主，按脚本机械新建一条剪辑时间线为次；准入是本集至少有一个可用视频。 */
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

/** 本集剪辑时间线条数：优先取剪辑概况，取不到时按计划里的剪辑时间线 ID 计。 */
function editTimelineCount(facts: Facts, ctx: StepListContext): number {
  const ids = facts.plan.status.artifacts.edit_timelines?.timeline_ids;
  return ctx.editOverview?.timeline_count ?? (Array.isArray(ids) ? ids.length : 0);
}

/** 已有剪辑时间线时去剪辑视图的文字链；给出 `timelineId` 时切到那条剪辑时间线。 */
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
  // 剪辑是下一步时入口就地展开在下一步里；其余时候有正式脚本条目就常驻在本行，准入不满足时置灰。
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
    // 余下没有资产图的是现在生成不了的（缺描述，或衍生的本体缺图），原因见本行的提醒。
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

/** 下一步挂在哪一行。 */
function ownerRow(facts: Facts, rows: StepRowView[], action: WorkflowNextAction): StepRowKey | null {
  switch (action.type) {
    case "resolve_draft":
      return action.args.draft_kind === PROMPT_AUTHORING_DRAFT_KIND ? "prompts" : "plan";
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

/** 广告/短片「AI 生成脚本」：带上下一步的附加指令直接提交，结果写成正式脚本。 */
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
    case "resolve_draft": {
      const draft = content.drafts.find((d) => d.kind === action.args.draft_kind) ?? content.drafts[0];
      const docType = draft ? draftDocType(draft) : null;
      if (!draft || !docType) return { ...base, detail: t("workflow:next_detail_resolve_draft_unknown") };
      const surface: EpisodeSurface = draft.kind === PROMPT_AUTHORING_DRAFT_KIND ? "prompt_authoring_draft" : "script_plan";
      if (draft.needs_repair) {
        return {
          ...base,
          title: t("workflow:next_title_resolve_draft_repair"),
          detail: t("workflow:next_detail_resolve_draft_repair"),
          instruction: { initial: "", persist: null },
          primary: [
            { key: "agent", label: t("workflow:act_agent"), kind: "agent", intent: { type: "draft_to_agent", docType } },
            { key: "ai-repair", label: t("dashboard:draft_ai_repair"), kind: "ai", intent: { type: "repair_draft", docType } },
          ],
          alternatives: [
            { key: "edit-draft", label: t("workflow:act_edit_draft"), kind: "nav", intent: { type: "show_surface", surface } },
            ...(surface === "script_plan"
              ? [
                  {
                    key: "regenerate",
                    label: t("dashboard:script_plan_regenerate_open"),
                    kind: "nav",
                    intent: { type: "open_script_plan_over_draft" },
                  } satisfies StepAct,
                ]
              : []),
            { key: "discard-draft", label: t("dashboard:draft_discard_action"), kind: "danger", intent: { type: "discard_draft", docType } },
          ],
        };
      }
      return {
        ...base,
        title: t("workflow:next_title_resolve_draft_agent"),
        detail: t("workflow:next_detail_resolve_draft_agent"),
        primary: [
          agentAct(t, t("dashboard:draft_agent_finish_prefill", { episodeRef, docType }), t("dashboard:draft_agent_finish")),
        ],
        alternatives: [
          { key: "discard-draft", label: t("dashboard:draft_agent_discard"), kind: "danger", intent: { type: "discard_draft", docType } },
        ],
      };
    }
    case "prepare_script_plan":
      return {
        ...base,
        detail: t("workflow:next_detail_prepare_script_plan"),
        instruction: { initial: ctx.savedScriptPlanInstructions, persist: "script_plan" },
        primary: [
          { key: "agent", label: t("workflow:act_agent"), kind: "agent", intent: { type: "plan_script_to_agent" } },
          { key: "plan", label: t("dashboard:script_plan_open"), kind: "ai", intent: { type: "plan_script" } },
        ],
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
        agentAct(t, t("workflow:agent_prefill_repair_video_units", { episodeRef, ids: formatNameList(action.requested_ids, ctx.lang) })),
      ];
      if (first && ctx.canViewUnit) {
        primary.push({ key: "view", label: t("workflow:act_view_unit", { id: first }), kind: "nav", intent: { type: "view_unit", unitId: first } });
      }
      return {
        ...base,
        detail: t("workflow:next_detail_repair_video_units", { count, ids: formatNameList(action.requested_ids, ctx.lang) }),
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
      // 分镜图生视频的批量入口与时间线工具栏同一个确认框；宫格图与参考生视频各有自己的入口。
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
    case "configure_provider":
      return {
        ...base,
        primary: [
          { key: "settings", label: t("workflow:act_open_settings"), kind: "nav", intent: { type: "route", path: `~${ROUTE_APP_SETTINGS}` } },
        ],
      };
    case "confirm_request_duration":
      // 确认入口在视频行的整批准入陈述里。
      return base;
    default:
      return ADMISSION_ACTIONS.has(action.type)
        ? { ...base, primary: [agentAct(t, t("workflow:agent_prefill_admission", { episodeRef }))] }
        : base;
  }
}

/**
 * 引用了还没有资产图的资产时，生成入口会拒绝引用它们的分镜：下一步照常给出，另附一条跳转提示。
 * 提示只说数量，具体是哪些资产、为什么没有资产图由资产图行的提醒陈述。视频只在参考生视频模式直接引用资产。
 */
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
