import type { TFunction } from "i18next";

import type { EpisodeMeta, ProjectChange } from "@/types";
import { episodeDisplayName, episodeItemLabel } from "@/utils/episode-display";

const GROUP_NAME_LIMIT = 5;

/** 事件通知文案的翻译函数，由调用方从 `events` 命名空间取得。 */
export type EventsT = TFunction<"events">;

/** 把事件里的集 ID 与条目 ID 换成「标题 · S01」所需的账本，按播出顺序排列。 */
export type EpisodeLedger = readonly Pick<EpisodeMeta, "episode" | "title">[];

// 完成事件：一次生成跑完的信号。同时承担两种用途——
// 一、通知类别（action 本身即类别，与 entity_type 无关）：优先级查表、导航行为、通知文案均不按
//     entity_type 拆分，五类骨架/任务共用同一套判定；
// 二、计费信号：生成完成即可能产生供应商费用，命中即重拉成本。
export const COMPLETION_ACTIONS: ReadonlySet<ProjectChange["action"]> = new Set([
  "storyboard_ready",
  "video_ready",
  "grid_ready",
  "reference_video_ready",
  "tts_ready",
  "voice_sample_ready",
]);

export interface GroupedProjectChange {
  key: string;
  entityType: ProjectChange["entity_type"];
  action: ProjectChange["action"];
  changes: ProjectChange[];
}

export function buildEntityRevisionKey(
  entityType: ProjectChange["entity_type"],
  entityId: string,
): string {
  return `${entityType}:${entityId}`;
}

export function groupChangesByType(
  changes: ProjectChange[],
): GroupedProjectChange[] {
  const groups = new Map<string, GroupedProjectChange>();

  for (const change of changes) {
    const key = `${change.entity_type}:${change.action}`;
    const existing = groups.get(key);
    if (existing) {
      existing.changes.push(change);
      continue;
    }
    groups.set(key, {
      key,
      entityType: change.entity_type,
      action: change.action,
      changes: [change],
    });
  }

  return [...groups.values()];
}

// 条目 ID 带集前缀（E1S01）的实体；资产名是自由文本，形似条目 ID 也按原名显示。
const EPISODE_ITEM_ENTITY_TYPES = new Set(["segment", "drama_scene", "shot", "reference_unit", "grid"]);

// 事件携带的稳定 label_key + label_params 是文案真相源；label 是后端按默认语言渲染的兜底，
// 只在事件来自不认识该 key 的旧发布方时兜住，不参与常规渲染。
// 集 ID 与带集前缀的条目 ID 不直接露给创作者：集换成标题（或播出位置），条目换成「标题 · S01」。
function resolveChangeLabel(change: ProjectChange, t: EventsT, episodes: EpisodeLedger): string {
  if (!change.label_key) {
    return change.label;
  }
  const params: Record<string, unknown> = { ...change.label_params };
  if (typeof params.id === "string" && EPISODE_ITEM_ENTITY_TYPES.has(change.entity_type)) {
    params.id = episodeItemLabel(params.id, episodes, t);
  }
  if (typeof params.episode === "number") {
    params.episode_name = episodeDisplayName(episodes, params.episode, t);
  }
  return t(`label.${change.label_key}`, {
    ...params,
    defaultValue: change.label,
  });
}

function getEntityLabel(group: GroupedProjectChange, t: EventsT): string {
  if (group.action === "storyboard_ready") {
    return t("noun.storyboard_image");
  }
  if (group.action === "video_ready") {
    return t("noun.video");
  }
  if (group.action === "grid_ready" || group.action === "grid_split_done") {
    return t("noun.grid");
  }
  if (group.action === "tts_ready") {
    return t("noun.narration_audio");
  }
  if (group.action === "voice_sample_ready") {
    return t("noun.voice_sample");
  }
  return t(`entity.${group.entityType}`, {
    defaultValue: t("entity.fallback"),
  });
}

function getChangeListLabel(change: ProjectChange, t: EventsT, episodes: EpisodeLedger): string {
  if (
    change.entity_type === "character" ||
    change.entity_type === "scene" ||
    change.entity_type === "prop" ||
    change.entity_type === "product"
  ) {
    return change.entity_id;
  }
  if (
    change.entity_type === "segment" ||
    change.entity_type === "drama_scene" ||
    change.entity_type === "shot" ||
    change.entity_type === "reference_unit"
  ) {
    return episodeItemLabel(change.entity_id, episodes, t);
  }
  return resolveChangeLabel(change, t, episodes);
}

function summarizeGroupNames(group: GroupedProjectChange, t: EventsT, episodes: EpisodeLedger): string {
  const names = group.changes
    .slice(0, GROUP_NAME_LIMIT)
    .map((change) => getChangeListLabel(change, t, episodes));
  const suffix = group.changes.length > GROUP_NAME_LIMIT ? t("list.more_suffix") : "";
  return `${names.join(t("list.separator"))}${suffix}`;
}

// 单条文案的句式 key：action 决定句式，与分组文案共用同一套 action 归类。
function singleTextKey(action: ProjectChange["action"]): string {
  if (action === "storyboard_ready" || action === "video_ready") {
    return action;
  }
  if (
    action === "grid_ready" ||
    action === "reference_video_ready" ||
    action === "tts_ready" ||
    action === "voice_sample_ready"
  ) {
    return "generated";
  }
  if (action === "grid_split_done") {
    return "completed";
  }
  if (action === "created" || action === "deleted") {
    return action;
  }
  return "updated";
}

function groupTextKey(group: GroupedProjectChange): string {
  if (COMPLETION_ACTIONS.has(group.action)) {
    return "generated";
  }
  if (group.action === "created" || group.action === "deleted") {
    return group.action;
  }
  return "updated";
}

export function formatGroupedNotificationText(
  group: GroupedProjectChange,
  t: EventsT,
  episodes: EpisodeLedger = [],
): string {
  if (group.changes.length === 1) {
    const change = group.changes[0];
    return t(`single.${singleTextKey(change.action)}`, {
      label: resolveChangeLabel(change, t, episodes),
    });
  }

  return t(`group.${groupTextKey(group)}`, {
    count: group.changes.length,
    entity: getEntityLabel(group, t),
    summary: summarizeGroupNames(group, t, episodes),
  });
}

export function formatGroupedDeferredText(
  group: GroupedProjectChange,
  t: EventsT,
  episodes: EpisodeLedger = [],
): string {
  if (group.changes.length === 1) {
    const change = group.changes[0];
    return t(`deferred.${singleTextKey(change.action)}`, {
      label: resolveChangeLabel(change, t, episodes),
    });
  }

  return t(`group_deferred.${groupTextKey(group)}`, {
    count: group.changes.length,
    entity: getEntityLabel(group, t),
    summary: summarizeGroupNames(group, t, episodes),
  });
}
