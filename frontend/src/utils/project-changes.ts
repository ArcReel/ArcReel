import type { ProjectChange } from "@/types";

const GROUP_NAME_LIMIT = 5;

const ENTITY_LABELS: Record<ProjectChange["entity_type"], string> = {
  project: "项目",
  character: "角色",
  clue: "线索",
  segment: "分镜",
  episode: "剧集",
  overview: "项目概览",
  draft: "预处理",
};

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

export function buildVersionResourceRevisionKey(
  resourceType: "storyboards" | "videos" | "characters" | "clues",
  resourceId: string,
): string {
  if (resourceType === "storyboards" || resourceType === "videos") {
    return buildEntityRevisionKey("segment", resourceId);
  }
  if (resourceType === "characters") {
    return buildEntityRevisionKey("character", resourceId);
  }
  return buildEntityRevisionKey("clue", resourceId);
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

function getEntityLabel(group: GroupedProjectChange): string {
  if (group.action === "storyboard_ready") {
    return "分镜图";
  }
  if (group.action === "video_ready") {
    return "视频";
  }
  return ENTITY_LABELS[group.entityType] ?? "内容";
}

function getChangeListLabel(change: ProjectChange): string {
  if (
    change.entity_type === "character" ||
    change.entity_type === "clue" ||
    change.entity_type === "segment"
  ) {
    return change.entity_id;
  }
  return change.label;
}

function summarizeGroupNames(group: GroupedProjectChange): string {
  const names = group.changes.slice(0, GROUP_NAME_LIMIT).map(getChangeListLabel);
  const suffix = group.changes.length > GROUP_NAME_LIMIT ? "…等" : "";
  return `${names.join("、")}${suffix}`;
}

function formatSingleNotificationText(change: ProjectChange): string {
  if (change.action === "storyboard_ready") {
    return `${change.label}的分镜图已生成`;
  }
  if (change.action === "video_ready") {
    return `${change.label}的视频已生成`;
  }
  if (change.action === "created") {
    return `${change.label}已创建`;
  }
  if (change.action === "deleted") {
    return `${change.label}已删除`;
  }
  return `${change.label}已更新`;
}

function formatSingleDeferredText(change: ProjectChange): string {
  if (change.action === "storyboard_ready") {
    return `AI 刚生成了 ${change.label} 的分镜图，点击查看`;
  }
  if (change.action === "video_ready") {
    return `AI 刚生成了 ${change.label} 的视频，点击查看`;
  }
  if (change.action === "created") {
    return `AI 刚新增了 ${change.label}，点击查看`;
  }
  if (change.action === "deleted") {
    return `AI 刚删除了 ${change.label}，点击查看`;
  }
  return `AI 刚更新了 ${change.label}，点击查看`;
}

export function formatGroupedNotificationText(
  group: GroupedProjectChange,
): string {
  if (group.changes.length === 1) {
    return formatSingleNotificationText(group.changes[0]);
  }

  const count = group.changes.length;
  const entityLabel = getEntityLabel(group);
  const summary = summarizeGroupNames(group);

  if (group.action === "storyboard_ready" || group.action === "video_ready") {
    return `已生成 ${count} 个${entityLabel}：${summary}`;
  }
  if (group.action === "created") {
    return `新增了 ${count} 个${entityLabel}：${summary}`;
  }
  if (group.action === "deleted") {
    return `删除了 ${count} 个${entityLabel}：${summary}`;
  }
  return `更新了 ${count} 个${entityLabel}：${summary}`;
}

export function formatGroupedDeferredText(
  group: GroupedProjectChange,
): string {
  if (group.changes.length === 1) {
    return formatSingleDeferredText(group.changes[0]);
  }

  const count = group.changes.length;
  const entityLabel = getEntityLabel(group);
  const summary = summarizeGroupNames(group);

  if (group.action === "storyboard_ready" || group.action === "video_ready") {
    return `AI 刚生成了 ${count} 个${entityLabel}：${summary}，点击查看`;
  }
  if (group.action === "created") {
    return `AI 刚新增了 ${count} 个${entityLabel}：${summary}，点击查看`;
  }
  if (group.action === "deleted") {
    return `AI 刚删除了 ${count} 个${entityLabel}：${summary}，点击查看`;
  }
  return `AI 刚更新了 ${count} 个${entityLabel}：${summary}，点击查看`;
}
