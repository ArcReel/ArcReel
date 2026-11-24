import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { CopyButton } from "@/components/shared/CopyButton";
import { InfoTip, InfoTipContent, InfoTipTrigger } from "@/components/ui/info-tip";
import type { EpisodeItemKind } from "@/utils/episode-item";

interface ItemPositionOfTotalProps {
  itemId: string;
  kind: EpisodeItemKind;
  position: number;
  total: number;
  className?: string;
}

export function ItemPositionOfTotal({ itemId, kind, position, total, className }: ItemPositionOfTotalProps) {
  const { t } = useTranslation("common");
  const label = t(`item_position_${kind}`, { position });
  const internalId = t("item_internal_id", { id: itemId });
  return (
    <InfoTip>
      <InfoTipTrigger
        aria-label={`${label} / ${total}`}
        render={
          <button
            type="button"
            className={cn(
              "num inline-flex h-5 shrink-0 items-center rounded-4xl bg-primary px-2 text-xs font-medium text-primary-foreground",
              className,
            )}
          />
        }
      >
        {t("item_position_of_total", { position, total })}
      </InfoTipTrigger>
      <InfoTipContent title={label}>
        <span className="flex items-center gap-1.5">
          <span translate="no" className="font-mono select-all">
            {internalId}
          </span>
          <CopyButton text={itemId} />
        </span>
      </InfoTipContent>
    </InfoTip>
  );
}
