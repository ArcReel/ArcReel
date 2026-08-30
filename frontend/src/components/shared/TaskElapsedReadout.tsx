import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useNowTick } from "@/hooks/useNowTick";
import { elapsedDisplay, taskElapsed, totalTaskElapsed } from "@/utils/task-elapsed";
import type { ElapsedDisplay, TaskElapsed, TaskTiming } from "@/utils/task-elapsed";
import { isTerminalStatus } from "@/types";

type Translate = (key: string, options?: Record<string, unknown>) => string;

const LABEL_KEYS: Record<TaskElapsed["kind"], string> = {
  running: "elapsed_running",
  queued: "elapsed_queued",
  total: "elapsed_total",
};

function formatDuration(display: ElapsedDisplay, t: Translate): string {
  switch (display.unit) {
    case "seconds":
      return t("elapsed_seconds", { seconds: display.seconds });
    case "minutes":
      return t("elapsed_minutes", { minutes: display.minutes, seconds: display.seconds });
    case "hours":
      return t("elapsed_hours", { hours: display.hours, minutes: display.minutes });
  }
}

interface Props {
  task: TaskTiming;
  className?: string;
  style?: CSSProperties;
}

/**
 * 任务时长读数：行内是裸数字，完整语义（已运行 / 已等待 / 耗时）走 title 与
 * 无障碍名。任务行与任务胶囊都已密到没有第二个词的余地，而三种时长在同一行里
 * 由任务状态本身区分，行内再复述一遍状态是冗余。
 *
 * 颜色与字号交给调用方（`className`），组件只保证等宽数字——读数每秒变化，
 * 比例字宽会让整行随之抖动。
 */
export function TaskElapsedReadout({ task, className, style }: Props) {
  // 已定格的时长不订阅时钟：终态任务在列表里往往占多数，为它们每秒重渲染一次纯属浪费。
  // 分支按状态而非按算得的时长决定——终态但时间戳缺失时同样什么都不渲染，不该因为
  // 「算不出来」就退回到订阅时钟的那一支。
  if (isTerminalStatus(task.status)) {
    const elapsed = totalTaskElapsed(task);
    if (elapsed === null) return null;
    return <ElapsedText elapsed={elapsed} className={className} style={style} />;
  }
  return <LiveElapsedReadout task={task} className={className} style={style} />;
}

function LiveElapsedReadout({ task, className, style }: Props) {
  const now = useNowTick();
  const elapsed = taskElapsed(task, now);
  if (elapsed === null) return null;
  return <ElapsedText elapsed={elapsed} className={className} style={style} />;
}

function ElapsedText({
  elapsed,
  className,
  style,
}: {
  elapsed: TaskElapsed;
  className?: string;
  style?: CSSProperties;
}) {
  const { t } = useTranslation("common");
  const duration = formatDuration(elapsedDisplay(elapsed.ms), t);
  const label = t(LABEL_KEYS[elapsed.kind], { duration });
  return (
    <span className={`num ${className ?? ""}`} style={style} title={label} aria-label={label}>
      {duration}
    </span>
  );
}
