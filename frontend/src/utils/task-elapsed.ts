import { parseIsoTimestamp } from "./date-format";
import { isTerminalStatus } from "@/types";
import type { TaskStatus } from "@/types";

export interface TaskTiming {
  status: TaskStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type TaskElapsedKind = "queued" | "running" | "total";

export interface TaskElapsed {
  kind: TaskElapsedKind;
  ms: number;
}

function timestampMs(value: string | null): number | null {
  if (!value) return null;
  const ms = parseIsoTimestamp(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

export function totalTaskElapsed(task: TaskTiming): TaskElapsed | null {
  const started = timestampMs(task.started_at);
  const finished = timestampMs(task.finished_at);
  if (started === null || finished === null) return null;
  return { kind: "total", ms: Math.max(0, finished - started) };
}

export function taskElapsed(task: TaskTiming, now: number): TaskElapsed | null {
  if (isTerminalStatus(task.status)) return totalTaskElapsed(task);
  if (task.status === "running") {
    const started = timestampMs(task.started_at);
    if (started === null) return null;
    return { kind: "running", ms: Math.max(0, now - started) };
  }
  const queued = timestampMs(task.queued_at);
  if (queued === null) return null;
  return { kind: "queued", ms: Math.max(0, now - queued) };
}

export type ElapsedDisplay =
  | { unit: "seconds"; seconds: number }
  | { unit: "minutes"; minutes: number; seconds: number }
  | { unit: "hours"; hours: number; minutes: number };

export function elapsedDisplay(ms: number): ElapsedDisplay {
  const totalSeconds = Math.floor(Math.max(0, ms) / 1000);
  if (totalSeconds < 60) return { unit: "seconds", seconds: totalSeconds };
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) {
    return { unit: "minutes", minutes: totalMinutes, seconds: totalSeconds % 60 };
  }
  return { unit: "hours", hours: Math.floor(totalMinutes / 60), minutes: totalMinutes % 60 };
}

export type ElapsedTranslate = (key: string, params: Record<string, number>) => string;

export function formatElapsedMs(ms: number, t: ElapsedTranslate): string {
  const display = elapsedDisplay(ms);
  switch (display.unit) {
    case "seconds":
      return t("common:elapsed_seconds", { seconds: display.seconds });
    case "minutes":
      return t("common:elapsed_minutes", {
        minutes: display.minutes,
        seconds: display.seconds,
      });
    case "hours":
      return t("common:elapsed_hours", { hours: display.hours, minutes: display.minutes });
  }
}
