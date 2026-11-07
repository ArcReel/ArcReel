import { useTranslation } from "react-i18next";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";

/**
 * Units whose requests have already been accepted by the provider.
 *
 * Use a separate row instead of folding this into task-status wording: acceptance means retries may bill again, while status answers
 * how far this attempt has progressed. If several tasks at the same step were accepted, state it once and list units.
 * Hide the row when no task was accepted; without this fact, do not show empty wording.
 */
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
