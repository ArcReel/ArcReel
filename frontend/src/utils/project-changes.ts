import type { ProjectChange } from "@/types";

const GROUP_NAME_LIMIT = 5;

// 生成事件（用于刷新费用等）——不含 grid_ready：宫格生成完成不触发费用刷新（既有行为）。
export const GENERATION_ACTIONS: ReadonlySet<ProjectChange["action"]> = new Set([
  "storyboard_ready",
  "video_ready",
  "reference_video_ready",
  "tts_ready",
]);

// 完成事件（action 本身即通知类别，与 entity_type 无关）——优先级查表、导航行为、通知文案均不按
// entity_type 拆分，五类骨架/任务共用同一套判定。
export const COMPLETION_ACTIONS: ReadonlySet<ProjectChange["action"]> = new Set([
  ...GENERATION_ACTIONS,
  "grid_ready",
]);

const ENTITY_LABELS: Record<ProjectChange["entity_type"], string> = {
  project: "项目",
  character: "角色",
  scene: "场景",
  prop: "道具",
  segment: "分镜",
  drama_scene: "场景",
  shot: "镜头",
  reference_unit: "视频单元",
  episode: "剧集",
  overview: "项目概览",
  draft: "预处理",
  grid: "宫格",
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
  if (group.action === "grid_ready") {
    return "宫格";
  }
  if (group.action === "tts_ready") {
    return "旁白";
  }
  return ENTITY_LABELS[group.entityType] ?? "内容";
}

function getChangeListLabel(change: ProjectChange): string {
  if (
    change.entity_type === "character" ||
    change.entity_type === "scene" ||
    change.entity_type === "prop" ||
    change.entity_type === "segment" ||
    change.entity_type === "drama_scene" ||
    change.entity_type === "shot" ||
    change.entity_type === "reference_unit"
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
  if (
    change.action === "grid_ready" ||
    change.action === "reference_video_ready" ||
    change.action === "tts_ready"
  ) {
    return `${change.label}已生成`;
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
  if (
    change.action === "grid_ready" ||
    change.action === "reference_video_ready" ||
    change.action === "tts_ready"
  ) {
    return `${change.label} 已生成`;
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

  if (COMPLETION_ACTIONS.has(group.action)) {
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

  if (COMPLETION_ACTIONS.has(group.action)) {
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
