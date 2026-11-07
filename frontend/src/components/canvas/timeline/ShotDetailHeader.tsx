import type { ReactNode } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ItemPositionOfTotal } from "@/components/shared/episode-item/ItemPositionOfTotal";
import { TooltipIconButton } from "./TooltipIconButton";

interface ShotDetailHeaderProps {
  segmentId: string;
  index: number;
  total: number;
  /** Properties after position: duration, status, break toggle and similar fields. */
  meta: ReactNode;
  /** Move one position earlier/later; hide if omitted. */
  onMoveEarlier?: () => void;
  onMoveLater?: () => void;
  /** Insert after and Remove storyboard. */
  structureActions?: ReactNode;
  onPrev: () => void;
  onNext: () => void;
  /** Shared disabling conditions/reasons for switching, moving, adding and deleting shots. */
  navDisabled: boolean;
  navDisabledHint?: string;
  /** Trailing notes entry. */
  notes?: ReactNode;
}

/** Storyboard-detail header: position / total and properties left; movement, add/delete, previous/next shot and notes right. */
export function ShotDetailHeader({
  segmentId,
  index,
  total,
  meta,
  onMoveEarlier,
  onMoveLater,
  structureActions,
  onPrev,
  onNext,
  navDisabled,
  navDisabledHint,
  notes,
}: ShotDetailHeaderProps) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2.5 gap-y-1.5 border-b border-border/50 px-5 py-2.5">
      <ItemPositionOfTotal itemId={segmentId} kind="shot" position={index + 1} total={total} />
      {meta}
      <div className="ml-auto flex items-center gap-0.5">
        {onMoveEarlier && onMoveLater ? (
          <>
            <TooltipIconButton
              label={t("shot_move_earlier")}
              hint={navDisabledHint}
              disabled={navDisabled || index === 0}
              onClick={onMoveEarlier}
            >
              <ChevronUp aria-hidden />
            </TooltipIconButton>
            <TooltipIconButton
              label={t("shot_move_later")}
              hint={navDisabledHint}
              disabled={navDisabled || index === total - 1}
              onClick={onMoveLater}
            >
              <ChevronDown aria-hidden />
            </TooltipIconButton>
          </>
        ) : null}
        {structureActions}
        <TooltipIconButton
          label={t("shot_detail_prev")}
          hint={navDisabledHint}
          disabled={navDisabled}
          onClick={onPrev}
          shortcut="K"
        >
          <ChevronLeft aria-hidden />
        </TooltipIconButton>
        <TooltipIconButton
          label={t("shot_detail_next")}
          hint={navDisabledHint}
          disabled={navDisabled}
          onClick={onNext}
          shortcut="J"
        >
          <ChevronRight aria-hidden />
        </TooltipIconButton>
        {notes}
      </div>
    </div>
  );
}
