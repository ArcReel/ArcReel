import type { ProjectStatus, ProjectSummary } from "@/types";

/**
 * 大厅的筛选维度：按集进度分「进行中 / 已完成」，另有与之正交的「待修复」
 * （项目结构升级失败）。项目没有流水线阶段。
 */
export type LobbyFilter = "all" | "in_progress" | "completed" | "repair";
export const LOBBY_FILTERS = ["all", "in_progress", "completed", "repair"] as const satisfies readonly LobbyFilter[];

/**
 * 项目的集进度：没有集、制作中、项目完成。不是流水线阶段。项目完成要求已有的集全部完成，且整本源文
 * 没有未规划成集的原文，与项目顶栏的「全部完成」同一口径。源文是否规划完由账本命令记进项目摘要，大厅不读源文。
 */
export type ProjectProgress = "empty" | "in_progress" | "completed";

export function asProjectStatus(s: ProjectSummary["status"]): ProjectStatus | null {
  return s && "episodes_summary" in s ? (s as ProjectStatus) : null;
}

export function projectProgress(status: ProjectStatus | null): ProjectProgress {
  const episodes = status?.episodes_summary;
  if (!episodes || episodes.total === 0) return "empty";
  return episodes.completed >= episodes.total && !status.source_remaining ? "completed" : "in_progress";
}

export function matchesFilter(status: ProjectStatus | null, filter: LobbyFilter): boolean {
  if (filter === "all") return true;
  if (!status) return false;
  if (filter === "repair") return status.needs_repair;
  const completed = projectProgress(status) === "completed";
  return filter === "completed" ? completed : !completed;
}

/** 只有确实被阻断的项目才有原因可显示——健康项目上的残留原因不展示。 */
export function repairReasonOf(status: ProjectStatus | null): string | null {
  return status?.needs_repair ? (status.repair_reason ?? null) : null;
}

export type GreetingKey =
  | "lobby_hero_greeting_morning"
  | "lobby_hero_greeting_afternoon"
  | "lobby_hero_greeting_evening"
  | "lobby_hero_greeting_late";

export function greetingKeyOf(d = new Date()): GreetingKey {
  const h = d.getHours();
  if (h >= 5 && h < 11) return "lobby_hero_greeting_morning";
  if (h >= 11 && h < 14) return "lobby_hero_greeting_afternoon";
  if (h >= 14 && h < 22) return "lobby_hero_greeting_evening";
  return "lobby_hero_greeting_late";
}
