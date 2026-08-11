/**
 * Reference-to-video unit types — mirrors lib/script_models.py Pydantic models.
 *
 * One "unit" produces one rendered video clip. Each unit may contain 1-4 shots.
 */

import type { TransitionType } from "./script";

export type AssetKind = "product" | "character" | "scene" | "prop";

/** Project.json sheet field for each asset kind. Mirrors lib/asset_types.py SHEET_KEY. */
export const SHEET_FIELD: Record<AssetKind, "product_sheet" | "character_sheet" | "scene_sheet" | "prop_sheet"> = {
  product: "product_sheet",
  character: "character_sheet",
  scene: "scene_sheet",
  prop: "prop_sheet",
};

/** Project.json bucket for each asset kind. Mirrors lib/asset_types.py BUCKET_KEY. */
export const BUCKET_FIELD: Record<AssetKind, "products" | "characters" | "scenes" | "props"> = {
  product: "products",
  character: "characters",
  scene: "scenes",
  prop: "props",
};

export interface Shot {
  /** Raw prompt text including @mentions — shots carry no duration; the unit does. */
  text: string;
}

export interface ReferenceResource {
  type: AssetKind;
  /** Must already exist in the matching project.json asset bucket. */
  name: string;
}

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
  shots: Shot[];
  /** Ordered — position defines [图N] index in the final prompt */
  references: ReferenceResource[];
  /** Planning duration in seconds — provider request duration is resolved during precheck. */
  duration_seconds: number;
  transition_to_next: TransitionType;
  note: string | null;
  generated_assets: UnitGeneratedAssets;
  /** Problem shell or mixed-speech marker; generation is blocked until repaired. */
  needs_replan?: boolean;
}

/**
 * 时长取档预检结果。`adjustment` 说明申请秒数相对剧本编排的偏移方向：
 * `exact` 一致、`up` 成片更长、`down` 成片更短、`unconstrained` 能力不可解析（原样透传）。
 */
export interface ReferenceDurationPrecheck {
  /** 申请秒数与剧本编排不一致（up / down）时为 true，需先向用户确认 */
  needs_confirmation: boolean;
  /** 剧本编排时长（秒） */
  script_duration: number;
  /** 将向模型申请的档位秒数 */
  request_duration: number;
  adjustment: "exact" | "up" | "down" | "unconstrained";
}

/**
 * 分镜文稿的读时派生结果——编辑器解析预览面板的内容源。
 *
 * 文稿是唯一真相：shots / references / utterances 都是机械派生物，不落盘。
 * `warnings` 已按请求语言渲染成文本（`key` 保留供测试与埋点定位）。
 */
/** 1-based 镜头序号；台词归属镜头级，时序对位由归属给出。 */
export type ScriptPreviewUtterance =
  | { shot_index: number; kind: "dialogue"; speaker: string; text: string }
  | { shot_index: number; kind: "voiceover"; speaker: null; text: string };

export interface ScriptPreviewWarning {
  key: string;
  message: string;
}

export interface ScriptPreview {
  shots: { index: number; text: string }[];
  /** 顺序即参考图编号；规范台词行的 speaker 位不计入 */
  references: ReferenceResource[];
  utterances: ScriptPreviewUtterance[];
  warnings: ScriptPreviewWarning[];
}

/**
 * reference_video step1 结构化中间态（审核 gate 的可审 / 可改对象）。映射后端
 * lib/script_models.py 的 ReferenceStep1Unit / ReferenceStep1Draft：step1 定内容层
 * （unit 边界 + unit 时长 + 各 shot 叙事文本 + 派生 references），step2 视觉编排由用户确认后才触发。
 * references 为服务端从 shot 文本 @ 引用机械派生（首现顺序决定 [图N] 编号），编辑正文保存时重派生，
 * 故审阅界面只读展示。
 */
export interface ReferenceStep1Unit {
  unit_id: string;
  shots: Shot[];
  references: ReferenceResource[];
  /** Unit duration in seconds — one generation call, one duration. */
  duration_seconds: number;
  /** 逐字原文摘录（追溯锚）；存量草稿可能为空串。 */
  source_text: string;
}

export interface ReferenceStep1Draft {
  units: ReferenceStep1Unit[];
}

/**
 * step1 的书写层扁平形状（隔离草稿装的是这个，不是落盘的 `ReferenceStep1Draft`）：
 * `unit_id` / `shots` / `references` 一律机器派生，落盘前才有——隔离期间只有时长 + 原文锚 +
 * 一段书写层正文。Mirrors lib/script_models.py ReferenceStep1FlatUnit / ReferenceStep1FlatDraft。
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
 * 隔离草稿违约条目。Mirrors lib/reference_video/quarantine.py::violation_entries。
 * `label` 形如 `"unit E1U02"`——数组下标 = 派生 unit 序号 - 1，可据此定位到 `content.units[i]`。
 * `line` 是该 unit 正文内 0-based 原始行号（与 `useShotPromptHighlight.ts` 的 `sourceLine` 同
 * 坐标系），仅语法类违约才有；unit 级违约（无自然行归属）为 null，呈现层落卡内聚合区。
 */
export interface ScriptReviewViolation {
  code: string;
  label: string;
  message: string;
  line: number | null;
}

/**
 * step1 隔离草稿信息（`ScriptReviewState.quarantine`）：reference_video 变体、隔离草稿在场时
 * 才非 null。`content` 是读时按同一校验器重算后的扁平产出（校验通过部分已收编，未通过部分原样
 * 呈现 agent 手改的文本）；`violations` 同样是读时重算的结果，不是草稿里上一轮的报告快照。
 */
export interface ScriptReviewQuarantine {
  /** null 仅在隔离草稿文件已损坏、无法解析信封形状时出现——`violations` 会带一条说明。 */
  content: ReferenceStep1FlatDraft | null;
  violations: ScriptReviewViolation[];
}

export interface ReferenceVideoScript {
  episode: number;
  title: string;
  /**
   * 内容类型——参考视频集继承项目级 narration/drama，决定画面比例等次级配置；
   * "视频来源"维度由项目的生成路线表达，不落在剧本上。
   */
  content_mode?: "narration" | "drama" | "ad";
  duration_seconds: number;
  schema_version?: number;
  novel: { title: string; chapter: string };
  video_units: ReferenceVideoUnit[];
}
