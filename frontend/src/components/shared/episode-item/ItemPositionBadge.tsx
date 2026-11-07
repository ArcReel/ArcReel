import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { EpisodeItemKind } from "@/utils/episode-item";

interface ItemPositionBadgeProps {
  /** Current position in this episode's array, starting at 1; null means the entry no longer exists in the script. */
  position: number | null;
  kind: EpisodeItemKind;
  /** Overlay on the thumbnail's top left; otherwise an inline label by default. */
  overlay?: boolean;
  /** Selected state: inline labels use the primary background. */
  active?: boolean;
  className?: string;
}

/**
 * Position badge for storyboard/video units: numbers only; accessible names supply the type, such as Storyboard 5 or Video unit 5.
 * Show Deleted when absent from the script rather than falling back to item IDs.
 */
export function ItemPositionBadge({ position, kind, overlay = false, active = false, className }: ItemPositionBadgeProps) {
  const { t } = useTranslation("common");
  const deleted = position === null;
  const label = deleted ? t("deleted") : t(`item_position_${kind}`, { position });
  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        "num inline-flex h-4.5 min-w-4.5 shrink-0 items-center justify-center rounded-sm px-1 text-xs leading-none font-semibold",
        overlay && "absolute top-0.5 left-0.5 bg-background/85 text-foreground",
        !overlay && (active ? "bg-primary text-primary-foreground" : "bg-muted text-subtle-foreground"),
        deleted && "font-normal text-muted-foreground",
        className,
      )}
    >
      <span aria-hidden>{deleted ? t("deleted") : position}</span>
    </span>
  );
}
