import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { useNowTick } from "@/hooks/useNowTick";
import { formatElapsedMs, taskElapsed, totalTaskElapsed } from "@/utils/task-elapsed";
import type { TaskElapsed, TaskTiming } from "@/utils/task-elapsed";
import { isTerminalStatus } from "@/types";

const LABEL_KEYS: Record<TaskElapsed["kind"], string> = {
  running: "elapsed_running",
  queued: "elapsed_queued",
  total: "elapsed_total",
};

interface Props {
  task: TaskTiming;
  className?: string;
  style?: CSSProperties;
}

export function TaskElapsedReadout({ task, className, style }: Props) {
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
  const duration = formatElapsedMs(elapsed.ms, t);
  const label = t(LABEL_KEYS[elapsed.kind], { duration });
  return (
    <span className={`num ${className ?? ""}`} style={style} title={label} aria-label={label}>
      {duration}
    </span>
  );
}
