import { useTranslation } from "react-i18next";
import { cn } from "cn";

import type { EpisodeItemKind } from "@/utils/episode-item";

interface ItemPositionBadgeProps {
  /** 本集数组里的当前位置，从 1 起；null 表示条目已不在剧本里。 */
  position: number | null;
  kind: EpisodeItemKind;
  /** 叠在缩略图左上角；缺省时作为行内标签。 */
  overlay?: boolean;
  /** 选中态：行内标签换成主色底。 */
  active?: boolean;
  className?: string;
}

/**
 * 分镜与视频单元的位置角标：只写数字，类型交给可访问名称（「分镜 5」「视频单元 5」）。
 * 条目已不在剧本里时写「已删除」，不回退成条目 ID。
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
