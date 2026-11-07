import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { CompareLane, LanePiece } from "./replan-compare-model";

/** New-plan bars alternate two brand-color tones, distinct from existing episodes' ID-based hues; unplanned source uses dashes. */
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
 * New-plan bar beside one source row, splitting height proportionally to character counts.
 * When `continues` is true, extend the last segment across paragraph spacing (`space-y-3`, 0.75rem) to join the next row.
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
  return (
    <>
      {pieces.map((piece, index) => {
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
      })}
    </>
  );
}

/** Changed boundary between rows: amber dashes span source text and its side bar, centered in paragraph spacing above the next row. */
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

/** Changed boundary within a row: insert an amber vertical dashed line before that character. */
export function BoundaryTick() {
  const { t } = useTranslation("dashboard");
  return (
    <span data-no-caret data-replan-diff className="relative inline-block h-[1.35em] w-0 align-text-bottom">
      <span aria-hidden className="absolute -left-px top-0 h-full border-l-2 border-dashed border-warn" />
      <span className="sr-only">{t("replan_boundary_differs")}</span>
    </span>
  );
}

/** The candidate has not reached this episode yet: Waiting for planning. */
export function WaitingBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex shrink-0 items-center rounded-sm border border-dashed border-input px-1.5 text-xs text-muted-foreground">
      {t("replan_waiting")}
    </span>
  );
}
