import { useTranslation } from "react-i18next";

import type { CompareLane, LanePiece } from "./replan-compare-model";

/** 新方案的色条：同一色相的深浅两档交替，与现有分集按集 ID 取色的多色相区分开。 */
export function newLaneColor(index: number): string {
  return index % 2 === 0 ? "oklch(0.8 0.09 295)" : "oklch(0.6 0.08 295)";
}

const PENDING_LANE = "repeating-linear-gradient(to bottom, var(--color-hairline-strong) 0 4px, transparent 4px 8px)";

function laneBackground(lane: CompareLane): string {
  return lane.kind === "pending" ? PENDING_LANE : newLaneColor(lane.index);
}

/** 段落间距（`space-y-3`）：与下一行属于同一集时色条向下延伸这么多，连成一条。 */
const PARAGRAPH_GAP = "0.75rem";

/**
 * 一行原文右侧的新方案色条：按这一行里各段原文的字数比例分高度。
 * `continues` 为 true 时最后一段延伸过段落间距，与下一行的色条连上。
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
        className="pointer-events-none absolute -right-5 w-[3px] rounded-full"
        style={{
          top: `${top}%`,
          bottom: extend ? `calc(${bottom}% - ${PARAGRAPH_GAP})` : `${bottom}%`,
          background: laneBackground(piece.lane),
        }}
      />
    );
  });
}

/** 落在两行之间的不同分界：横跨正文与右侧色条的琥珀色虚线，画在下一行的上方。 */
export function BoundaryRule() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      data-no-caret
      data-replan-diff
      className="pointer-events-none absolute -right-6 left-0 border-t border-dashed"
      style={{ top: `calc(${PARAGRAPH_GAP} / -2)`, borderColor: "var(--color-warm)" }}
    >
      <span className="sr-only">{t("replan_boundary_differs")}</span>
    </span>
  );
}

/** 落在一行中间的不同分界：插在那个字之前的琥珀色竖虚线。 */
export function BoundaryTick() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      data-no-caret
      data-replan-diff
      className="relative inline-block h-[1.35em] w-0 align-text-bottom"
      title={t("replan_boundary_differs")}
    >
      <span
        aria-hidden
        className="absolute -left-px top-0 h-full border-l-2 border-dashed"
        style={{ borderColor: "var(--color-warm)" }}
      />
      <span className="sr-only">{t("replan_boundary_differs")}</span>
    </span>
  );
}

/** 方案还没生成到这一集：「等待规划」。 */
export function WaitingBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-px text-[10.5px] leading-[1.6] text-text-3"
      style={{ border: "1px dashed var(--color-hairline-strong)" }}
    >
      {t("replan_waiting")}
    </span>
  );
}
