import { parseIsoTimestamp } from "./date-format";
import { isTerminalStatus } from "@/types";
import type { TaskStatus } from "@/types";

/**
 * Duration calculation needs task states/three timestamps, so accept structural inputs, not entire `TaskItem`:
 * workflow `WorkflowTaskObservation` lacks timestamps and must fetch matching task_id rows first.
 * Broad input shapes share one implementation across both consumers.
 */
export interface TaskTiming {
  status: TaskStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/**
 * Duration semantics describe distinct intervals: `queued` waits before starting, `running` remains
 * in progress, `total` is frozen whole-execution time. Separate copy prevents terminal
 * numbers appearing still running.
 */
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

/**
 * Whole-execution duration for terminal tasks; null for missing/unparseable start/end timestamps.
 *
 * Separate entries without `now` signal frozen intervals, letting callers avoid clock subscriptions
 * without calculating first and inspecting types afterward. Nonterminal tasks must not use this.
 */
export function totalTaskElapsed(task: TaskTiming): TaskElapsed | null {
  const started = timestampMs(task.started_at);
  const finished = timestampMs(task.finished_at);
  if (started === null || finished === null) return null;
  return { kind: "total", ms: Math.max(0, finished - started) };
}

/**
 * Currently displayable task durations, null when uncomputable, letting callers omit entire readouts.
 *
 * Null differs from 0 to distinguish just started from unknown; `0s` would disguise abnormal
 * missing timestamps as normal starts. Terminal durations ignore `now`, never increasing with timers.
 * Backward clocks (unsynchronized frontend/backend or future started_at) clamp to 0, never negatives.
 */
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

/**
 * Magnitude-based precision: seconds below minutes, minutes/seconds below hours, otherwise hours/minutes.
 * Never milliseconds, which add no useful elapsed-task information and distract through rapid changes.
 */
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

/**
 * Duration-copy selector, requiring minimal interpolated `common` lookup shapes only;
 * any namespace `t` can pass directly because keys always use `common:` prefixes here.
 */
export type ElapsedTranslate = (key: string, params: Record<string, number>) => string;

/**
 * Duration copy, with units/order from `common:elapsed_*`, shared sitewide:
 * equal intervals must read identically in tasks and usage tables.
 */
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
