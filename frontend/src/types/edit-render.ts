/**
 * 剪辑时间线出片相关的类型：成片与剪映草稿的产物现状、提交结果。
 *
 * 对应后端 server/routers/edit_timelines.py 的响应形态。
 */

import type { EditTimelineIssue, TimelineNarration } from "./edit-timeline";

/** 产物相对于剪辑时间线的时效；stale 仍可下载。 */
export type RenderArtifactStatus = "current" | "stale" | "missing" | "blocked";

/** 出片的两种交付物。 */
export type RenderKind = "final_cut" | "jianying_draft";

/** 成片是否把字幕烧入画面。 */
export type SubtitleMode = "burned_subtitles" | "no_subtitles";

/** 出片选项：旁白版本两种交付物都有，烧入字幕只有成片。 */
export interface RenderOptions {
  narration: TimelineNarration;
  subtitles: SubtitleMode;
}

/** 剪映版本：6 表示 6 及以上，5 表示 5.x。 */
export type JianyingVersion = "5" | "6";

/** 出片入口只关心 issue 的级别、影响范围与定位；完整形态见剪辑视图的读取结果。 */
export type EditTimelineIssueRef = Pick<EditTimelineIssue, "code" | "severity" | "applies_to" | "unit_id">;

interface RenderArtifactStatusBase {
  episode: number;
  timeline_id: string;
  status: RenderArtifactStatus;
  artifact_path: string;
  version: number | null;
  rendered_at: string | null;
}

/** 交付检查一行的状态。 */
export type DeliveryRowStatus = "pass" | "warn" | "fail";

/** 交付检查的一行：字段、期望与实测、文案键与参数（前端按 locale 渲染）。 */
export interface DeliveryRow {
  field: string;
  status: DeliveryRowStatus;
  expected: string | null;
  actual: string | null;
  hint_key: string | null;
  hint_params: Record<string, string | number>;
}

/** 一次平台交付检查的完整结果；按请求平台（缺省按画幅推导）检查。 */
export interface DeliveryCheck {
  platform: string;
  rows: DeliveryRow[];
}

export interface FinalCutStatus extends RenderArtifactStatusBase {
  narration: TimelineNarration;
  subtitles: SubtitleMode;
  /** 只在文件存在时给出；走匿名媒体路由，可直接下载。 */
  download_url: string | null;
  /** 按画幅推导的默认平台（或请求指定平台）的交付检查；没有成片文件时没有。 */
  delivery: DeliveryCheck | null;
  /** 成片联系表的预览地址；没有联系表时没有。 */
  preview_url: string | null;
}

export interface JianyingDraftStatus extends RenderArtifactStatusBase {
  narration: TimelineNarration;
}

export interface RenderSubmission {
  task_id: string;
  deduped: boolean;
  artifact_path: string;
}
