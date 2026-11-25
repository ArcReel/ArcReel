import type { TFunction } from "i18next";

import type { EpisodeMeta, ProjectChange } from "@/types";
import {
  episodeDisplayName,
  episodeItemLabel,
  formatEpisodeItemLocation,
  type ProjectScripts,
} from "@/utils/episode-display";

const GROUP_NAME_LIMIT = 5;

export type EventsT = TFunction<"events">;

export type EpisodeLedger = readonly Pick<EpisodeMeta, "episode" | "title">[];

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

const EPISODE_ITEM_ENTITY_TYPES = new Set(["segment", "drama_scene", "shot", "reference_unit", "grid"]);

function changeItemLabel(
  itemId: string,
  change: ProjectChange,
  t: EventsT,
  episodes: EpisodeLedger,
  scripts: ProjectScripts,
): string {
  if (change.position !== undefined) {
    return formatEpisodeItemLocation(
      itemId,
      {
        episode: change.episode ?? null,
        item_id: change.item_id ?? itemId,
        position: change.position,
        status: change.position === null ? "deleted" : "found",
      },
      episodes,
      t,
    );
  }
  return episodeItemLabel(itemId, scripts, episodes, t);
}

function resolveChangeLabel(change: ProjectChange, t: EventsT, episodes: EpisodeLedger, scripts: ProjectScripts): string {
  if (!change.label_key) {
    return change.label;
  }
  const params: Record<string, unknown> = { ...change.label_params };
  if (typeof params.id === "string" && EPISODE_ITEM_ENTITY_TYPES.has(change.entity_type)) {
    params.id = changeItemLabel(params.id, change, t, episodes, scripts);
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

function getChangeListLabel(change: ProjectChange, t: EventsT, episodes: EpisodeLedger, scripts: ProjectScripts): string {
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
    return changeItemLabel(change.item_id ?? change.entity_id, change, t, episodes, scripts);
  }
  return resolveChangeLabel(change, t, episodes, scripts);
}

function summarizeGroupNames(
  group: GroupedProjectChange,
  t: EventsT,
  episodes: EpisodeLedger,
  scripts: ProjectScripts,
): string {
  const names = group.changes
    .slice(0, GROUP_NAME_LIMIT)
    .map((change) => getChangeListLabel(change, t, episodes, scripts));
  const suffix = group.changes.length > GROUP_NAME_LIMIT ? t("list.more_suffix") : "";
  return `${names.join(t("list.separator"))}${suffix}`;
}

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
  scripts: ProjectScripts = {},
): string {
  if (group.changes.length === 1) {
    const change = group.changes[0];
    return t(`single.${singleTextKey(change.action)}`, {
      label: resolveChangeLabel(change, t, episodes, scripts),
    });
  }

  return t(`group.${groupTextKey(group)}`, {
    count: group.changes.length,
    entity: getEntityLabel(group, t),
    summary: summarizeGroupNames(group, t, episodes, scripts),
  });
}

export function formatGroupedDeferredText(
  group: GroupedProjectChange,
  t: EventsT,
  episodes: EpisodeLedger = [],
  scripts: ProjectScripts = {},
): string {
  if (group.changes.length === 1) {
    const change = group.changes[0];
    return t(`deferred.${singleTextKey(change.action)}`, {
      label: resolveChangeLabel(change, t, episodes, scripts),
    });
  }

  return t(`group_deferred.${groupTextKey(group)}`, {
    count: group.changes.length,
    entity: getEntityLabel(group, t),
    summary: summarizeGroupNames(group, t, episodes, scripts),
  });
}
