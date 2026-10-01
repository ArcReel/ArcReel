/**
 * 剪辑时间线的读取形态，与 lib/edit_timeline/readout.py 与 service.py 的 TimelineSummary 同形。
 * 时间一律以秒为单位，最多三位小数；绝对起点与时长由服务端算好。
 */

/** 成片、剪映草稿与预览按带不带旁白分两个版本。 */
export type TimelineNarration = "with_narration" | "without_narration";

export type EditTimelineAuthorKind = "creator" | "arcreel_agent" | "external_agent";

export interface EditTimelineAuthor {
  kind: EditTimelineAuthorKind;
  user_id: string | null;
}

/** 剪辑时间线的指称：ID 与集内不重名的显示名。 */
export interface EditTimelineRef {
  id: string;
  name: string;
}

/** 剪辑时间线列表中的一条（`GET /projects/{name}/edit-timelines`）；`updated_*` 描述最新修订。 */
export interface EditTimelineSummary extends EditTimelineRef {
  episode: number;
  revision: number;
  clip_count: number;
  created_at: string;
  updated_at: string;
  updated_by: EditTimelineAuthor;
  update_summary: string;
  agent_turn: string | null;
}

/** `unit_deleted` 渲染跳过、时长计 0；`video_missing` 时长暂按编排时长占位。 */
export type EditClipStatus = "ready" | "video_missing" | "unit_deleted";

export interface EditClipTrim {
  source_in: number;
  source_out: number;
  basis_version: number;
}

export interface EditClipTransition {
  type: string;
  duration: number;
}

export interface EditClipNarration {
  start: number;
  end: number | null;
}

export interface EditClip {
  id: string;
  unit_id: string;
  status: EditClipStatus;
  start: number;
  /** 截取后的画面时长加定格延长。 */
  duration: number;
  video_version: number | null;
  /** current 视频全长；没有可用视频时为 null。 */
  source_duration: number | null;
  trim: EditClipTrim | null;
  source_volume: number;
  hold: number;
  carries_narration: boolean;
  narration: EditClipNarration | null;
  reason: string | null;
  transition_to_next: EditClipTransition | null;
}

/** BGM 片段。`end` 是截到时间线末尾后的实际结束时间；`fade_in` / `fade_out` 是摆放后实际生效的淡入淡出。 */
export interface EditBgmClip {
  id: string;
  bgm_id: string;
  /** 所引用 BGM 的名称；BGM 已不在项目里时为 null。 */
  name: string | null;
  start: number;
  end: number;
  source_in: number;
  source_out: number;
  volume: number;
  fade_in: number;
  fade_out: number;
}

export type EditTimelineIssueCode =
  | "trim_ignored"
  | "unit_deleted"
  | "unit_unused"
  | "video_missing"
  | "hold_too_long"
  | "narration_missing"
  | "narration_overrun"
  | "narration_source_collision"
  | "subtitle_missing_glyphs"
  | "bgm_missing";

export interface EditTimelineIssue {
  code: EditTimelineIssueCode;
  severity: "info" | "warning" | "blocking";
  applies_to: "all" | "with_narration";
  clip_ids: string[];
  unit_id: string | null;
  params: Record<string, unknown>;
}

export interface EditTimelineReadout {
  timeline: EditTimelineRef & { episode: number };
  revision: number;
  latest_revision: number;
  duration: number;
  clips: EditClip[];
  bgm: EditBgmClip[];
  issues: EditTimelineIssue[];
}

/** 一集的剪辑概况（`GET /projects/{name}/episodes/{episode}/edit-overview`）。 */
export interface EpisodeEditOverview {
  episode: number;
  timeline_count: number;
  /** 最近修改的那条及其问题数；还没有剪辑时间线时为 null。 */
  latest: (EditTimelineRef & { updated_at: string; issue_count: number }) | null;
  /** 成片已落后于剪辑时间线的那几条；从未出片的不在其中。 */
  stale_final_cuts: EditTimelineRef[];
}

/** 预览用的一条字幕：时间相对单元，跟随旁白时从旁白起点算起，否则按视频源素材时间。 */
export interface EditPreviewCue {
  start: number;
  duration: number;
  text: string;
}

/** 旁白配音 current 文件的项目内路径与版本号。 */
export interface EditPreviewNarrationAudio {
  path: string;
  version: number;
}

export interface EditPreviewUnitMedia {
  unit_id: string;
  /** 该单元 current 视频生成时的供应商原声开关；为 false 时文件里即使带音轨，预览也静音，与成片同口径。 */
  provider_audio: boolean;
  /** 只在该单元按带旁白版本呈现时给出。 */
  narration_audio: EditPreviewNarrationAudio | null;
  subtitles_follow_narration: boolean;
  subtitles: EditPreviewCue[];
}

/** BGM 文件的项目内路径与响度静态增益（线性倍数）；预览音量是片段音量乘以这个增益。 */
export interface EditPreviewBgm {
  bgm_id: string;
  path: string;
  gain: number;
}

/** 剪辑视图预览的素材层，与 server/services/presentation/timeline_preview.py 同形。 */
export interface EditTimelinePreviewMedia {
  timeline_id: string;
  revision: number;
  narration: TimelineNarration;
  units: EditPreviewUnitMedia[];
  /** 只含 BGM 轨引用且文件在项目里的 BGM。 */
  bgm: EditPreviewBgm[];
}

/** 项目里的一首 BGM（`GET /projects/{name}/bgm` 与上传结果）；`gain` 是把响度统一到 −16 LUFS 的线性增益。 */
export interface ProjectBgm {
  id: string;
  name: string;
  duration: number;
  gain: number;
  path: string;
  url: string;
}
