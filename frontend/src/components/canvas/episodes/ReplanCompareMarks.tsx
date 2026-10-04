import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { CompareLane, LanePiece } from "./replan-compare-model";

/** 新方案的色条：品牌色深浅两档交替，与现有分集按集 ID 取色的多色相区分开；方案还没生成到的原文是虚线。 */
function laneClass(lane: CompareLane) {
  return cn(
    lane.kind === "pending"
      ? "border-l-3 border-dashed border-input"
      : lane.index % 2 === 0
        ? "w-0.75 rounded-full bg-primary"
        : "w-0.75 rounded-full bg-primary/55",
  );
}

/**
 * 一行原文右侧的新方案色条：按这一行里各段原文的字数比例分高度。
 * `continues` 为 true 时最后一段延伸过段落间距（`space-y-3`，0.75rem），与下一行的色条连上。
 */
export function LaneBar({
  pieces,
  start,
  end,
  continues,
}: {
  pieces: LanePiece[];
  start: number;
  end: number;
  continues: boolean;
}) {
  const length = Math.max(end - start, 1);
  return pieces.map((piece, index) => {
    const top = ((piece.from - start) / length) * 100;
    const bottom = 100 - ((piece.to - start) / length) * 100;
    const extend = continues && index === pieces.length - 1;
    return (
      <span
        key={piece.from}
        aria-hidden
        data-replan-lane={piece.lane.kind}
        className={cn("pointer-events-none absolute -right-5 top-(--lane-top) bottom-(--lane-bottom)", laneClass(piece.lane))}
        style={
          {
            "--lane-top": `${top}%`,
            "--lane-bottom": extend ? `calc(${bottom}% - 0.75rem)` : `${bottom}%`,
          } as CSSProperties
        }
      />
    );
  });
}

/** 落在两行之间的不同分界：横跨正文与右侧色条的琥珀色虚线，画在下一行上方的段落间距正中。 */
export function BoundaryRule() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      data-no-caret
      data-replan-diff
      className="pointer-events-none absolute -top-1.5 -right-6 left-0 border-t border-dashed border-warn"
    >
      <span className="sr-only">{t("replan_boundary_differs")}</span>
    </span>
  );
}

/** 落在一行中间的不同分界：插在那个字之前的琥珀色竖虚线。 */
export function BoundaryTick() {
  const { t } = useTranslation("dashboard");
  return (
    <span data-no-caret data-replan-diff className="relative inline-block h-[1.35em] w-0 align-text-bottom">
      <span aria-hidden className="absolute -left-px top-0 h-full border-l-2 border-dashed border-warn" />
      <span className="sr-only">{t("replan_boundary_differs")}</span>
    </span>
  );
}

/** 方案还没生成到这一集：「等待规划」。 */
export function WaitingBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex shrink-0 items-center rounded-sm border border-dashed border-input px-1.5 text-xs text-muted-foreground">
      {t("replan_waiting")}
    </span>
  );
}
