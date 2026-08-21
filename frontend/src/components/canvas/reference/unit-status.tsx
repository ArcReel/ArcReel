import { useTranslation } from "react-i18next";
import { isActiveStatus } from "@/stores/tasks-store";
import type { ReferenceVideoUnit, TaskItem, UnitStatus } from "@/types";

export interface UnitStatusConf {
  i18nKey: string;
  textClass: string;
  bgClass: string;
  dotClass: string;
  pulse: boolean;
}

export const STATUS_CONF: Record<UnitStatus, UnitStatusConf> = {
  pending: {
    i18nKey: "reference_status_pending",
    textClass: "text-[var(--color-text-4)]",
    bgClass: "bg-[oklch(0.30_0.01_250_/_0.4)]",
    dotClass: "bg-[var(--color-text-4)]",
    pulse: false,
  },
  running: {
    i18nKey: "reference_status_running",
    textClass: "text-amber-300",
    bgClass: "bg-amber-500/15",
    dotClass: "bg-amber-400",
    pulse: true,
  },
  ready: {
    i18nKey: "reference_status_ready",
    textClass: "text-emerald-300",
    bgClass: "bg-emerald-500/15",
    dotClass: "bg-emerald-400",
    pulse: false,
  },
  failed: {
    i18nKey: "reference_status_failed",
    textClass: "text-red-300",
    bgClass: "bg-red-500/15",
    dotClass: "bg-red-400",
    pulse: false,
  },
};

/** 从画布传下来的 statusMap 取该单元状态；未提供时按有无成片退化为 ready/pending。 */
export function resolveUnitStatus(
  unit: ReferenceVideoUnit,
  statusMap?: Record<string, UnitStatus>,
): UnitStatus {
  return statusMap?.[unit.unit_id] ?? (unit.generated_assets.video_clip ? "ready" : "pending");
}

export interface UnitStatusInput {
  /** 该单元是否已有成片资产。 */
  hasClip: boolean;
  /** 该单元的最新任务行；「最新行胜出」由 selectLatestTaskByResource 保证。 */
  queueRow: TaskItem | undefined;
  /** 占用集命中——真实任务行占用或入队动作层的乐观标记，含 cancelling。 */
  busy: boolean;
  /** 该单元正在上传成片。 */
  uploading?: boolean;
  /**
   * 本画布是否提供手动上传成片入口。提供时成片可能来自上传而非生成，历史失败行不该
   * 覆盖已有的可播放资产；不提供时成片只能来自生成，最新行失败即本次尝试失败，必须
   * 展示失败而不能被旧成片压成 ready，否则失败原因无处可见。
   */
  supportsManualUpload?: boolean;
}

/**
 * 视频单元的展示状态：两个参考生视频画布共用的单一推导口径。
 *
 * 判定按优先级依次落位——上传中 → 队列行活跃 → 队列行失败 → 乐观占用窗口。
 * 展示语义用 isActiveStatus（cancelling 不显示为生成中），与占用语义
 * （isOccupyingStatus，用于禁用控件）是两个谓词，不要合并：取消中的单元不显示为
 * 「生成中」，但按钮仍须禁用，故 `busy` 独立于本函数的返回值另行接线。
 *
 * 乐观分支要求 `!queueRow`：有任务行时状态一律由该行决定，否则 cancelling 会被
 * 乐观占用重新显示成生成中。重试与重新生成这两条路径上任务行始终在，乐观窗口内的
 * 禁用因此不能只看本函数返回值——见 `busy` 的说明。
 */
export function deriveUnitStatus({
  hasClip,
  queueRow,
  busy,
  uploading = false,
  supportsManualUpload = false,
}: UnitStatusInput): UnitStatus {
  // 上传中视为 running：批量生成按状态挑 pending 目标，否则上传期间会被再次入队，
  // 与生成回写同一个成片文件。
  if (uploading) return "running";
  if (queueRow && isActiveStatus(queueRow.status)) return "running";
  // 失败任务行 DB 持久化、不会过期，故成片是否压过失败取决于成片能否来自上传。
  if (queueRow?.status === "failed" && !(supportsManualUpload && hasClip)) return "failed";
  if (!queueRow && busy) return "running";
  return hasClip ? "ready" : "pending";
}

export interface StatusBadgeProps {
  status: UnitStatus;
  /** sm = 4px dot + 10px label, md = 5px dot + 10.5px label. */
  size?: "sm" | "md";
}

export function StatusBadge({ status, size = "sm" }: StatusBadgeProps) {
  const { t } = useTranslation("dashboard");
  const conf = STATUS_CONF[status];
  const dotSize = size === "sm" ? "h-1 w-1" : "h-[5px] w-[5px]";
  const fontSize = size === "sm" ? "text-[10px]" : "text-[10.5px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 font-medium ${fontSize} ${conf.textClass} ${conf.bgClass}`}
    >
      <span
        aria-hidden="true"
        className={`${dotSize} rounded-full ${conf.dotClass} ${conf.pulse ? "motion-safe:animate-pulse" : ""}`}
      />
      {t(conf.i18nKey)}
    </span>
  );
}
