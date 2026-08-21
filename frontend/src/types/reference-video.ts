/**
 * Reference-to-video unit types — mirrors lib/script_models.py Pydantic models.
 *
 * One "unit" produces one rendered video clip. Its body (`text`) is the single
 * source of truth: reference images are resolved from the `@[名称]` mentions at
 * execution time and never persisted or transported.
 */

import type { TransitionType } from "./script";
import type {
  AdmissionProblem,
  VideoRequestCostQuote,
  BatchAdmissionDecision,
  BatchAdmissionTier,
  BatchAdmissionUnit,
  WorkflowAdmission,
} from "./workflow";

export type AssetKind = "product" | "character" | "scene" | "prop";

/** Project.json sheet field for each asset kind. Mirrors lib/asset_types.py SHEET_KEY. */
export const SHEET_FIELD: Record<AssetKind, "product_sheet" | "character_sheet" | "scene_sheet" | "prop_sheet"> = {
  product: "product_sheet",
  character: "character_sheet",
  scene: "scene_sheet",
  prop: "prop_sheet",
};

/**
 * Raw persisted status value returned by the backend in `generated_assets.status`.
 * Mirrors lib/script_models.py:GeneratedAssets.status Pydantic Literal exactly.
 * Note: "storyboard_ready" never appears for reference_video units — it's a legacy
 * storyboard-mode value retained in the shared GeneratedAssets model.
 */
export type UnitPersistedStatus = "pending" | "storyboard_ready" | "completed";

/**
 * UI-derived status shown in the UnitList status dot and preview panel.
 * Composed from (persisted status + task-queue state + error signals) by UI code.
 * Not sent to or received from the backend.
 */
export type UnitStatus = "pending" | "running" | "ready" | "failed";

export interface UnitGeneratedAssets {
  storyboard_image: string | null;
  storyboard_last_image: string | null;
  grid_id: string | null;
  grid_cell_index: number | null;
  video_clip: string | null;
  video_uri: string | null;
  video_thumbnail?: string | null;
  narration_audio?: string | null;
  /** Raw backend status — use `UnitStatus` for UI display. */
  status: UnitPersistedStatus;
  /** ISO8601 completion time; null is treated as "before any voice setting". */
  video_generated_at: string | null;
  /** Legacy migration history only; runtime never reads or creates it. */
  source_signature?: string | null;
}

export interface ReferenceVideoUnit {
  /** Format: "E{episode}U{index}" */
  unit_id: string;
  /** Unit body — free-form text carrying `@[名称]` mentions; the only persisted content truth. */
  text: string;
  /** Planning duration in seconds — provider request duration is resolved during precheck. */
  duration_seconds: number;
  transition_to_next: TransitionType;
  note: string | null;
  generated_assets: UnitGeneratedAssets;
  /** Problem shell or mixed-speech marker; generation is blocked until repaired. */
  needs_replan?: boolean;
}

export interface ReferenceRequestOptions {
  narration_delivery?: "post_production" | "use_tts";
}

export interface ReferenceGenerationRequestOptions extends ReferenceRequestOptions {
  /** Exact video tier accepted for this request; omitted when no cross-tier confirmation is needed. */
  confirmed_request_duration_seconds?: number | null;
}

export interface ReferenceProjectionLocation {
  path: (string | number)[];
  line: number | null;
}

export interface ReferenceProjectionProblem {
  code: string;
  blocking: boolean;
  unit_id: string;
  locations: ReferenceProjectionLocation[];
  params: Record<string, unknown>;
  reason?: string;
  action: string;
  message?: string;
}

export interface ReferenceProjectionAdmission {
  allowed: false;
  kind: "reference_request_projection";
  unit_id: string;
  problems: ReferenceProjectionProblem[];
}

export type { VideoRequestCostQuote } from "./workflow";

/** Current-state duration admission returned before a storyboard video is enqueued. */
export interface NarratedVideoDurationAdmission {
  allowed: false;
  kind: "narrated_video_duration";
  unit_id: string;
  narration_delivery: Record<string, unknown>;
  planned_duration: number;
  current_visual_duration?: number | null;
  duration_input: number;
  request_duration: number | null;
  adjustment: "exact" | "up" | "down" | null;
  request_cost?: VideoRequestCostQuote;
  problems: ReferenceProjectionProblem[];
}

/**
 * 时长取档预检结果。`adjustment` 说明申请秒数相对取档输入的偏移方向：
 * `exact` 一致、`up` 成片更长、`down` 成片更短。能力元数据不可解析时预检直接失败。
 */
export interface ReferenceDurationPrecheck {
  /** 请求档位与当前视觉档位（无成片时为剧本档位）不一致时为 true */
  needs_confirmation: boolean;
  /** 剧本编排时长（秒） */
  script_duration: number;
  /** 当前选中且实际时长足够承载 fresh TTS 的视觉档位；没有可信成片时为 null */
  current_visual_duration?: number | null;
  /** 取档输入；使用 TTS 时为剧本时长与实际旁白时长下限的较大值 */
  duration_input: number;
  /** 将向模型申请的档位秒数 */
  request_duration: number;
  adjustment: "exact" | "up" | "down";
  declared_capability: "i2v" | "r2v";
  hydrated_capability: "i2v" | "r2v";
  provider_id: string | null;
  model_id: string | null;
  request_cost?: VideoRequestCostQuote;
  problems: ReferenceProjectionProblem[];
}

/**
 * 批量视频生成的准入结论——「全有或全无」：三种结局都是评估成功（HTTP 200），
 * 只有 `admitted` 创建了任务；`confirmation_required` 与 `blocked` 一个任务也没建。
 */
export type ReferenceBatchDecision = BatchAdmissionDecision;

/**
 * 单个目标单元的准入缺口。形状与工作流计划里的同一对象一致，故直接沿用
 * {@link AdmissionProblem}——两处讲的是同一件事，不各留一份定义。
 */
export type ReferenceBatchProblem = AdmissionProblem;

/**
 * 每个目标单元的结论。受阻时本身没有问题的单元也带一条
 * `generation_batch_admission_withheld`，其 params.blocked_unit_ids 指出是谁拦下的。
 */
export type ReferenceBatchUnitOutcome = BatchAdmissionUnit;

/**
 * 按申请档位分组的确认项；`cost_amount` 为 null 表示该档报价不全，不展示合计。
 * `request_duration_seconds` 为 null 表示该组档位未解析出来，界面按「档位待定」陈述。
 */
export type ReferenceBatchConfirmationTier = BatchAdmissionTier;

/**
 * 一个没能入队的目标。已创建的任务不因此被撤销，它们照常执行；这里列出的 unit
 * 本次没有任务、也没有计费，下次「缺失即生成」会正好补上它们。
 */
export interface ReferenceBatchEnqueueFailure {
  unit_id: string;
  problem: AdmissionProblem;
}

export interface ReferenceBatchAdmission extends WorkflowAdmission {
  skipped_unit_ids: string[];
  /** 仅 admitted 时非空 */
  task_ids: string[];
  /** 逐 unit 的任务行，供调用方各自兑现自己的乐观占用标记。 */
  task_ids_by_unit: Record<string, string>;
  /** 入队中断时没轮到的 unit；整批入队成功时为空数组。 */
  enqueue_failures: ReferenceBatchEnqueueFailure[];
  deduped: boolean;
}

/** 批量端点请求体：省略 unit_ids 表示「缺失即生成」，空数组会被后端拒绝。 */
export interface ReferenceBatchGenerateRequest {
  unit_ids?: string[];
  /** 必填：不声明就等于让这次批量绕过旁白交付方式的选择。 */
  narration_delivery: "post_production" | "use_tts";
  /** 用户已确认的申请档位，按 unit 给 */
  confirmed_request_durations?: Record<string, number>;
}

/**
 * 视频单元正文的读时派生结果——编辑器解析预览面板的内容源。
 *
 * 正文是唯一真相：utterances 与参考图都是机械派生物，不落盘。
 * `warnings` 已按请求语言渲染成文本（`key` 保留供测试与埋点定位）。
 */
/** `index` 是 1-based 的 utterance 序号，按正文出现顺序编号。 */
export type ScriptPreviewUtterance =
  | { index: number; kind: "dialogue"; speaker: string; text: string }
  | { index: number; kind: "voiceover"; speaker: null; text: string };

export interface ScriptPreviewWarning {
  key: string;
  message: string;
}

export interface ScriptPreview {
  utterances: ScriptPreviewUtterance[];
  warnings: ScriptPreviewWarning[];
}

/**
 * reference_video step1 结构化中间态（内容确认的可审 / 可改对象）。映射后端
 * lib/script_models.py 的 ReferenceStep1Unit / ReferenceStep1Draft：step1 定内容层
 * （unit 边界 + unit 时长 + 单元正文），step2 视觉编排由用户确认后才触发。
 */
export interface ReferenceStep1Unit {
  unit_id: string;
  /** 单元正文，用 `@[名称]` 引用已登记资产。 */
  text: string;
  /** Unit duration in seconds — one generation call, one duration. */
  duration_seconds: number;
  /** 逐字原文摘录（追溯锚）；存量草稿可能为空串。 */
  source_text: string;
}

export interface ReferenceStep1Draft {
  units: ReferenceStep1Unit[];
}

/**
 * step1 的扁平草稿结构（草稿装的是这个，不是落盘的 `ReferenceStep1Draft`）：
 * `unit_id` 机器派生，落盘前才有——草稿中只有时长 + 原文锚 + 一段引用语法正文。
 * Mirrors lib/script_models.py ReferenceStep1FlatUnit / ReferenceStep1FlatDraft。
 */
export interface ReferenceStep1FlatUnit {
  duration_seconds: number;
  source_text: string;
  text: string;
}

export interface ReferenceStep1FlatDraft {
  units: ReferenceStep1FlatUnit[];
}

/**
 * 草稿违约条目。Mirrors lib/draft_quarantine.py::violation_entries。
 * `label` 是定位前缀，形如 `"unit E1U02"`（参考生视频，数组下标 = 派生 unit 序号 - 1）或
 * `"segment E1S03"`（narration，与 `segment_id` 对应）；集级违约无定位、为空串。
 * `line` 是该单元正文内 0-based 原始行号（与 `useUnitPromptHighlight.ts` 的 `sourceLine` 同
 * 坐标系），仅语法类违约才有；单元级违约（无自然行归属）为 null，呈现层落卡内聚合区。
 */
export interface ScriptReviewViolation {
  code: string;
  label: string;
  message: string;
  line: number | null;
  locations?: Array<{ path: Array<string | number>; line: number | null }>;
  reason?: string;
  action?: string;
}

/**
 * step1 草稿信息（`ScriptReviewState.quarantine`）：草稿在场时才非 null，三条 step1
 * 路线都可能出现。`content` 是读时按同一校验器重算后的草稿层内容（校验通过部分已收编，未通过
 * 部分原样呈现 Agent 手改的文本）；`violations` 同样是读时重算的结果，不是草稿里上一轮的报告
 * 快照。
 *
 * `content` 的形状随路线不同（参考生视频 `{ units }`、drama `{ title, scenes }`、narration
 * `{ segments }`），且草稿正是给 Agent 手改的那一份——字段可能缺失或类型不对。故这里只声明到
 * 「一个对象」，各面板按自己那条路线逐项收窄后渲染，不信任声明。
 */
export interface ScriptReviewQuarantine {
  /** null 仅在草稿文件已损坏、无法解析信封形状时出现——`violations` 会带一条说明。 */
  content: Record<string, unknown> | null;
  violations: ScriptReviewViolation[];
}

export interface ReferenceVideoScript {
  episode: number;
  title: string;
  /**
   * 内容类型——参考生视频剧本继承项目级 narration/drama，决定画面比例等次级配置；
   * "视频来源"维度由项目的生成模式表达，不落在剧本上。
   */
  content_mode?: "narration" | "drama" | "ad";
  duration_seconds: number;
  schema_version?: number;
  novel: { title: string; chapter: string };
  video_units: ReferenceVideoUnit[];
}
