/**
 * 工作流计划的前端投影。字段与后端 `lib/workflow_plan.py` / `lib/workflow_state.py` /
 * `lib/generation_result.py` 一一对应，REST 与 MCP 共用同一序列化，顶层无 envelope。
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

/** 本次请求的旁白交付方式；不写回项目，只作用于这一次生成。 */
export type NarrationDelivery = "post_production" | "use_tts";

/** 逐项结果的判定。requested 被这三者穷尽划分。 */
export type GenerationItemState = "succeeded" | "failed" | "blocked";

/** 任务这一轴的下场，与产物时效独立。 */
export type GenerationTaskState =
  | "not_queued"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";

/**
 * 后端给出的下一步动作标识。取值是开放集合（编排层会派生动态类型），
 * 界面按已知值分派、其余走兜底陈述，不做穷举断言。
 */
export type WorkflowActionType = string;

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

export interface GenerationItemResult {
  unit_id: string;
  state: GenerationItemState;
  artifact_key?: string | null;
  artifact_path?: string | null;
  task_id?: string | null;
  task_state: GenerationTaskState;
  artifact_status?: ArtifactStatus | null;
  provider_checkpoint?: ProviderCheckpoint | null;
  problem?: GenerationProblem | null;
}

export interface GenerationSkippedItem {
  unit_id: string;
  artifact_key?: string | null;
  artifact_path?: string | null;
  artifact_status?: ArtifactStatus | null;
}

/** 批量执行的逐项结果：requested 被 succeeded / failed / blocked 穷尽划分。 */
export interface GenerationBatchResult {
  schema_version: 1;
  operation: string;
  selection: "explicit" | "missing_only";
  requested: string[];
  succeeded: string[];
  failed: string[];
  blocked: string[];
  skipped: GenerationSkippedItem[];
  items: GenerationItemResult[];
}

/**
 * 一个步骤下的产物集合。集合可枚举时给三份 id 列表；容器本身读不了时
 * 只给 `state: "blocked"`，此时不猜任何 id 落进 current / stale / missing。
 * 单份产物（剧本、step1 等）只给 `state`。
 */
export interface WorkflowArtifactCollection {
  state?: ArtifactStatus | "not_applicable";
  current_ids?: string[];
  stale_ids?: string[];
  missing_ids?: string[];
  path?: string;
  [key: string]: unknown;
}

export interface WorkflowStepContracts {
  script_edit?: "script_batch_edit/v1" | null;
  batch_admission?: "video_batch_admission/v1" | null;
  generation_result?: "generation_result/v1" | null;
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
  generation_result?: GenerationBatchResult | null;
  contracts: WorkflowStepContracts;
}

/**
 * 批量准入结论。`admitted` 之外的两种结局都**一个任务也没建**，
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
  current_duration_seconds?: number | null;
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
  narration_delivery: NarrationDelivery;
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

export type WorkflowStateName =
  | "PROJECT_INPUT"
  | "SELLING_POINTS"
  | "ASSET_INVENTORY"
  | "EPISODE_PLAN"
  | "STEP1_CONTENT"
  | "STEP1_REVIEW"
  | "FINAL_SCRIPT"
  | "ASSET_SHEETS"
  | "STORYBOARD"
  | "VIDEO"
  | "EXPORT_READY";

export interface WorkflowStatus {
  schema_version: 1;
  project_revision: string;
  source_revision: string | null;
  project: WorkflowProject;
  target: WorkflowTarget | null;
  state: WorkflowStateName;
  blockers: WorkflowBlocker[];
  gates: Record<string, Record<string, unknown>>;
  artifacts: Record<string, WorkflowArtifactCollection>;
  next_action: WorkflowNextAction;
}

/**
 * 旁白交付选择的呈现契约：`persisted: false` 是后端的明示——
 * 这个选择只作用于本次请求，界面不能把它讲成项目设置。
 */
export interface WorkflowNarrationDeliveryChoice {
  selected: NarrationDelivery | null;
  options: NarrationDelivery[];
  persisted: false;
}

export interface WorkflowPlan {
  schema_version: 1;
  status: WorkflowStatus;
  narration_delivery: WorkflowNarrationDeliveryChoice;
  steps: WorkflowPlanStep[];
  blockers: WorkflowBlocker[];
  problems: GenerationProblem[];
  next_action: WorkflowNextAction;
}

/** `POST /projects/{name}/workflow-plan` 的请求体。 */
export interface WorkflowPlanRequest {
  episode?: number | null;
  narration_delivery?: NarrationDelivery | null;
  confirmed_request_durations?: Record<string, number>;
}
