import { useTranslation } from "react-i18next";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";

export function CheckpointNote({ unitIds }: { unitIds: string[] }) {
  const { t } = useTranslation("workflow");
  if (unitIds.length === 0) return null;
  return (
    <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      {unitIds.map((unitId) => (
        <EpisodeItemRef key={unitId} itemId={unitId} />
      ))}
      <span>{t("checkpoint_submitted")}</span>
    </p>
  );
}
