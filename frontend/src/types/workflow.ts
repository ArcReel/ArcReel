/**
 * 工作流计划的前端投影。字段与后端 `lib/workflow/workflow_plan.py` / `lib/workflow/workflow_state.py` /
 * `lib/generation/generation_result.py` 一一对应，REST 与 MCP 共用同一序列化，顶层无 envelope。
 *
 * 三条轴在类型层就分开，界面不得把它们折成一个状态：
 * - 步骤进度 `WorkflowStepState` —— 编排走到哪一步
 * - 产物时效 `ArtifactStatus` —— 磁盘上那份东西还能不能代表当前内容
 * - 任务与 provider checkpoint —— 这一次尝试的下场，以及供应商侧是否已经收单
 */

/** 编排步骤自身的进度，与产物、任务两轴无关。 */
export type WorkflowStepState =
  | "completed"
  | "ready"
  | "active"
  | "blocked"
  | "pending"
  | "skipped";

/** 产物时效。stale 是「可继续使用的告警」，不是缺失，也不授权自动重生。 */
export type ArtifactStatus = "current" | "stale" | "missing" | "blocked";

/** 旁白交付方式：项目配置（docs/adr/0089）。 */
export type NarrationDelivery = "post_production" | "use_tts";

/**
 * 后端给出的下一步动作标识的闭集，与 `lib/workflow/workflow_state.py` 的 `WorkflowActionType`
 * 一一对应，后端契约测试守住两侧同步。列成运行时数组而不只是类型，是为了让译文覆盖
 * 检查能逐个遍历：新增动作没配文案时测试直接红，而不是静默落到兜底陈述。
 */
export const WORKFLOW_ACTION_TYPES = [
  "none",
  "collect_project_input",
  "create_episode",
  "draft_selling_points",
  "analyze_assets",
  "plan_episodes",
  "reset_episode_planning",
  "resolve_draft",
  "prepare_script_plan",
  "start_blank_script",
  "provide_episode_source",
  "confirm_script_plan",
  "generate_script",
  "add_script_items",
  "author_prompts",
  "generate_asset_sheets",
  "generate_storyboards",
  "generate_grid",
  "repair_video_units",
  "generate_videos",
  "create_edit_timeline",
  "retry_project_migration",
  "patch_episode_script",
  "retry",
  "fix_input",
  "generate_dependency",
  "wait_for_task",
  "replan_unit",
  "confirm_request_duration",
  "configure_provider",
  "repair_artifact_state",
  "retry_artifact_download",
] as const;

export type WorkflowActionType = (typeof WORKFLOW_ACTION_TYPES)[number];

export interface WorkflowNextAction {
  type: WorkflowActionType;
  args: Record<string, unknown>;
  requested_ids: string[];
  requires_confirmation: boolean;
  reason: string;
}

/**
 * 数据损坏一类的阻断。`path` 指出具体字段位置，`reason` 是技术细节——
 * 前者进摘要，后者进折叠区。
 */
export interface WorkflowBlocker {
  code: string;
  path: string;
  reason: string;
}

/** 一条结构化问题：原因（code/detail）与下一步动作（action）分开表达。 */
export interface GenerationProblem {
  code: string;
  detail: string;
  action: string;
  params: Record<string, unknown>;
}

/** 供应商侧是否已经收单。已收单意味着重试可能重复计费，必须与任务状态分开陈述。 */
export interface ProviderCheckpoint {
  submitted: boolean;
  provider_id?: string | null;
  provider_job_id?: string | null;
}

/** 一次进行中的任务观察。恢复中的任务停在这条轴上，绝不计入 current 产物。 */
export interface WorkflowTaskObservation {
  unit_id: string;
  task_id: string;
  task_type: string;
  status: string;
  provider_checkpoint?: ProviderCheckpoint | null;
  problem?: GenerationProblem | null;
}

/**
 * 计划里产物条目的状态词。比 {@link ArtifactStatus} 宽：产物时效之外还要表达
 * 「本模式不涉及」与「只覆盖了部分范围」（如资产盘点只盘了一部分源文）。
 * 取值随后端演进，界面按已知值分派、其余走兜底陈述，不做穷举断言。
 */
export type WorkflowArtifactState = ArtifactStatus | "not_applicable" | "partial" | (string & {});

/**
 * 一个步骤下的产物集合。集合可枚举时给三份 id 列表；容器本身读不了时
 * 只给 `state: "blocked"`，此时不猜任何 id 落进 current / stale / missing。
 * 单份产物（剧本、script_plan 等）只给 `state`。
 */
export interface WorkflowArtifactCollection {
  state?: WorkflowArtifactState;
  current_ids?: string[];
  stale_ids?: string[];
  missing_ids?: string[];
  path?: string;
  [key: string]: unknown;
}

export interface WorkflowStepContracts {
  script_edit?: "script_batch_edit/v1" | null;
  batch_admission?: "video_batch_admission/v1" | null;
}

export interface WorkflowPlanStep {
  id: string;
  state: WorkflowStepState;
  required: boolean;
  action?: WorkflowNextAction | null;
  requested_ids: string[];
  artifacts: WorkflowArtifactCollection;
  problems: GenerationProblem[];
  tasks: WorkflowTaskObservation[];
  admission?: WorkflowAdmission | null;
  contracts: WorkflowStepContracts;
}

/**
 * 整批准入判定的结论。`admitted` 之外的两种结局都**一个任务也没建**，
 * 界面必须把这一点说清楚，而不是只报一句失败。
 */
export type BatchAdmissionDecision = "admitted" | "confirmation_required" | "blocked";

/**
 * 准入结论里的单条缺口。与 {@link GenerationProblem} 同源，差别只在于
 * 批量端点会把文案按用户语言渲染进 `message`——计划端点不渲染，界面据此回退到译文表。
 */
export interface AdmissionProblem {
  code: string;
  detail?: string | null;
  action?: string | null;
  params?: Record<string, unknown>;
  /** 已按用户语言本地化，可直接展示；缺省时按 `code` 查译文表。 */
  message?: string | null;
}

/** 供应商侧对这一次视频请求的精确报价。 */
export interface VideoRequestCostQuote {
  amount: number;
  currency: string;
  provider_id: string;
  model_id: string;
  request_duration_seconds: number;
}

export interface BatchAdmissionUnit {
  unit_id: string;
  /** 该单元自身的判定；受阻批次里它仍可能为 true（被同批其它单元连带扣下）。 */
  admitted: boolean;
  withheld?: boolean;
  request_duration_seconds?: number | null;
  request_cost?: VideoRequestCostQuote | null;
  problems: AdmissionProblem[];
  projection?: unknown;
}

export interface BatchAdmissionTier {
  request_duration_seconds: number | null;
  unit_count: number;
  unit_ids: string[];
  cost_amount?: number | null;
  cost_currency?: string | null;
}

export interface WorkflowAdmission {
  decision: BatchAdmissionDecision;
  operation: string;
  selection: "explicit" | "missing_only";
  units: BatchAdmissionUnit[];
  confirmation?: { tiers: BatchAdmissionTier[] } | null;
}

export interface WorkflowProject {
  content_mode: string;
  generation_mode: string;
  grid_storyboard: boolean;
}

export interface WorkflowTarget {
  episode: number;
  script: string;
  script_filename: string;
  source: string;
}

/** 一个 AI 操作此刻的结构准入；`reason` 是稳定的理由码，与操作入口的拒绝理由同源。 */
export interface WorkflowOperation {
  state: "admitted" | "refused" | "not_applicable";
  reason?: string | null;
}

/** 目标集上在场的一份草稿；`needs_repair` 区分待修复草稿与 Agent 的可编辑草稿。 */
export interface WorkflowDraft {
  kind: string;
  path: string;
  needs_repair: boolean;
}

/** 内容现状。集级字段只在有目标集时有值；项目整体不可用时整份为空。 */
export interface WorkflowContent {
  episode_count: number;
  whole_source: "present" | "absent" | "not_applicable";
  source_remaining: boolean;
  ad_inputs: "present" | "absent" | "not_applicable";
  products_without_selling_points: string[];
  episode_source?: "present" | "absent" | "not_applicable" | null;
  episode_plan_stale: boolean;
  expected_stale_script_plan_revision?: string | null;
  drafts: WorkflowDraft[];
  formal_script?: "present" | "absent" | "invalid" | null;
  script_item_count?: number | null;
  pending_authoring_ids: string[];
  needs_replan_ids: string[];
  referenced_assets_without_sheet: string[];
  unregistered_references: string[];
}

/** 上一次跑完的项目迁移没能登记的一件产物及原因。 */
export interface WorkflowMigrationSkippedArtifact {
  kind: string;
  episode: number | null;
  resource_id: string;
  artifact_path: string;
  reason: string;
}

/**
 * 上一次跑完的项目迁移登记与跳过了什么。只作说明，不参与状态与阻断；
 * 迁移失败的项目走 `blockers` 里的 `project_migration_failed`，这里为空。
 */
export interface WorkflowMigrationReport {
  schema_version: 1;
  migrated_at: string;
  from_schema_version: number;
  to_schema_version: number;
  registered: Record<string, number>;
  skipped: WorkflowMigrationSkippedArtifact[];
}

/**
 * 制作状态：内容现状（`content` / `artifacts`）、各 AI 操作的准入（`operations`）与建议的下一步
 * （`next_action`，分岔处的并列选项在 `next_alternatives`）。`blockers` 只表示项目整体不可用，
 * 内容本身的数据问题在 `issues`。
 */
export interface WorkflowStatus {
  schema_version: 2;
  project_revision: string;
  source_revision: string | null;
  project: WorkflowProject;
  target: WorkflowTarget | null;
  blockers: WorkflowBlocker[];
  issues: WorkflowBlocker[];
  content: WorkflowContent | null;
  operations: Record<string, WorkflowOperation>;
  gates: Record<string, Record<string, unknown>>;
  artifacts: Record<string, WorkflowArtifactCollection>;
  next_action: WorkflowNextAction;
  next_alternatives: WorkflowNextAction[];
  migration_report?: WorkflowMigrationReport | null;
}

/** 账本中一集建议的下一步（`GET /projects/{name}/workflow-status/episodes`），按账本顺序排列。 */
export interface EpisodeNextStep {
  episode: number;
  /** 该集的集规划已失效（原文已重新规划），下一步为 `none`。 */
  plan_stale: boolean;
  next_action: WorkflowNextAction;
}

export interface WorkflowPlan {
  schema_version: 2;
  status: WorkflowStatus;
  steps: WorkflowPlanStep[];
  blockers: WorkflowBlocker[];
  problems: GenerationProblem[];
  next_action: WorkflowNextAction;
  next_alternatives: WorkflowNextAction[];
}

/** `POST /projects/{name}/workflow-plan` 的请求体。 */
export interface WorkflowPlanRequest {
  episode_id?: number | null;
  confirmed_request_durations?: Record<string, number>;
}
