/**
 * 剪辑时间线出片相关的类型：成片与剪映草稿的产物现状、提交结果。
 *
 * 对应后端 server/routers/edit_timelines.py 的响应形态。
 */

import type { EditTimelineIssue } from "./edit-timeline";

/** 产物相对于剪辑时间线的时效；stale 仍可下载。 */
export type RenderArtifactStatus = "current" | "stale" | "missing" | "blocked";

/** 出片的两种交付物。 */
export type RenderKind = "final_cut" | "jianying_draft";

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

export interface FinalCutStatus extends RenderArtifactStatusBase {
  narration: string;
  subtitles: string;
  /** 只在文件存在时给出；走匿名媒体路由，可直接下载。 */
  download_url: string | null;
}

export interface JianyingDraftStatus extends RenderArtifactStatusBase {
  narration: "without_narration" | "with_narration";
}

export interface RenderSubmission {
  task_id: string;
  deduped: boolean;
  artifact_path: string;
}

/** 新建剪辑时间线的响应只取出片入口用到的身份字段。 */
export interface CreatedEditTimeline {
  timeline: { id: string; name: string; episode: number };
  revision: number;
}
