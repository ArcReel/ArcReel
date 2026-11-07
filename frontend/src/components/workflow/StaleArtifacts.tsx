import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useItemPositionLabeler } from "@/components/shared/episode-item/useEpisodeItem";
import { EpisodeItemRef } from "@/components/shared/episode-item/EpisodeItemRef";

interface Props {
  staleIds: string[];
  /** Navigate to canvas units with existing previews/version history. */
  onView?: (unitId: string) => void;
  /** Explicit regeneration; absent callbacks state statuses without creating unresponsive buttons. */
  onRegenerate?: (unitIds: string[]) => void;
  /** Pending regeneration disables sibling controls within the same step. */
  busy?: boolean;
}

/**
 * Stale but still usable artifacts.
 *
 * Retention is default; regeneration requires personal clicks. Stale means artifacts precede current content, while files remain
 * usable in finished outputs without blocking episode export. Never automatically regenerate or count them as gaps;
 * offer Inspect and Deliberately redo entries only, the latter costing money and requiring user clicks.
 */
export function StaleArtifacts({ staleIds, onView, onRegenerate, busy }: Props) {
  const { t } = useTranslation("workflow");
  const itemLabel = useItemPositionLabeler();
  const headingId = useId();
  if (staleIds.length === 0) return null;

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1.5 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2">
      <h4 id={headingId} className="text-xs font-medium text-warn">
        {t("stale_title", { count: staleIds.length })}
      </h4>
      <p className="text-xs leading-relaxed text-subtle-foreground">{t("stale_still_usable")}</p>
      <ul className="flex flex-col gap-1">
        {staleIds.map((unitId) => (
          <li key={unitId} className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <EpisodeItemRef itemId={unitId} />
            {onView && (
              <Button
                variant="link"
                size="xs"
                onClick={() => onView(unitId)}
                aria-label={t("stale_view_unit", { id: itemLabel(unitId) })}
              >
                {t("stale_view")}
              </Button>
            )}
            {onRegenerate && (
              <Button
                variant="link"
                size="xs"
                disabled={busy}
                onClick={() => onRegenerate([unitId])}
                aria-label={t("stale_regenerate_unit", { id: itemLabel(unitId) })}
              >
                {t("stale_regenerate")}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {onRegenerate && staleIds.length > 1 && (
        <div>
          <Button variant="outline" size="xs" disabled={busy} onClick={() => onRegenerate(staleIds)}>
            {t("stale_regenerate_all", { count: staleIds.length })}
          </Button>
        </div>
      )}
    </section>
  );
}
