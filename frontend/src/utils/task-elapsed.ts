import { parseIsoTimestamp } from "./date-format";
import { isTerminalStatus } from "@/types";
import type { TaskStatus } from "@/types";

/**
 * 时长计算只需要任务行上的状态与三个时间戳，故按结构取而不取整个 `TaskItem`——
 * 工作流面板的任务观测（`WorkflowTaskObservation`）不携带时间戳，须先从任务队列
 * 取回同一 task_id 的时间戳再喂进来，宽入参让两侧共用同一实现。
 */
export interface TaskTiming {
  status: TaskStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/**
 * 时长的语义。三者陈述的不是同一段区间：`queued` 是尚未开跑的等待，`running` 是
 * 仍在推进的运行，`total` 是已定格的整次执行耗时。文案必须分开，否则终态任务的
 * 数字会被读成「还在跑」。
 */
export type TaskElapsedKind = "queued" | "running" | "total";

export interface TaskElapsed {
  kind: TaskElapsedKind;
  ms: number;
}

const RUNNING_STATUSES: ReadonlySet<TaskStatus> = new Set(["running", "cancelling"]);

function timestampMs(value: string | null): number | null {
  if (!value) return null;
  const ms = parseIsoTimestamp(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/**
 * 终态任务的整次执行耗时；起止时间戳缺失或不可解析时返回 null。
 *
 * 单独成一个不收 `now` 的入口，是因为这段区间已经定格：调用方据此判断该读数无需订阅
 * 时钟，而不必先算一次时长再回头看它属于哪一种。非终态任务不该走这里。
 */
export function totalTaskElapsed(task: TaskTiming): TaskElapsed | null {
  const started = timestampMs(task.started_at);
  const finished = timestampMs(task.finished_at);
  if (started === null || finished === null) return null;
  return { kind: "total", ms: Math.max(0, finished - started) };
}

/**
 * 任务当前该呈现的时长；无法计算时返回 null，由调用方整块不渲染。
 *
 * 返回 null 而非 0 是为了区分「真的刚开始」与「算不出来」——后者若渲染成 `0s`，
 * 会把缺时间戳的异常任务伪装成正常起跑。终态时长与 `now` 无关，故不随计时器递增。
 * 时钟回拨（前后端时钟不同步、started_at 落在未来）截断为 0，不显示负数。
 */
export function taskElapsed(task: TaskTiming, now: number): TaskElapsed | null {
  if (isTerminalStatus(task.status)) return totalTaskElapsed(task);
  if (RUNNING_STATUSES.has(task.status)) {
    const started = timestampMs(task.started_at);
    if (started === null) return null;
    return { kind: "running", ms: Math.max(0, now - started) };
  }
  const queued = timestampMs(task.queued_at);
  if (queued === null) return null;
  return { kind: "queued", ms: Math.max(0, now - queued) };
}

/**
 * 按量级选精度的展示分解：不足一分钟给秒、不足一小时给分秒、其余给时分。
 * 毫秒一律不呈现——它对「任务跑了多久」这个判断没有信息量，只会让数字跳动刺眼。
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
