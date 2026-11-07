import type { ProjectStatus, ProjectSummary } from "@/types";

/**
 * Lobby filter dimensions: episode progress splits In progress / Completed; Needs repair is orthogonal
 * and means project-structure upgrade failure. Projects have no pipeline stages.
 */
export type LobbyFilter = "all" | "in_progress" | "completed" | "repair";
export const LOBBY_FILTERS = ["all", "in_progress", "completed", "repair"] as const satisfies readonly LobbyFilter[];

/**
 * Project episode progress: no episodes, in production or complete. These are not pipeline stages. Completion requires all existing episodes complete and
 * no whole-source text left unplanned, matching All complete in the project header. Ledger commands record source-planning completion in the project summary; the lobby does not read source text.
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

/** Display reasons only for genuinely blocked projects; suppress residual reasons on healthy projects. */
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
