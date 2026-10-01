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
}

/** 新建剪辑时间线返回的读取结果，这里只用到它的身份。 */
export interface EditTimelineReadout {
  timeline: EditTimelineRef & { episode: number };
  revision: number;
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
