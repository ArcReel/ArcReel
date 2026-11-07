import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Library, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { AssetSheetType } from "@/types";
import type { GalleryFilter } from "./gallery-model";

const FILTERS: GalleryFilter[] = ["all", "pending", "stale"];

/**
 * Gallery toolbar: title/count, sheet-status filters, batch generation, library selection, and creation.
 * Stick to the gallery scroll-region top; wrap controls as canvas width narrows.
 */
export function GalleryToolbar({
  assetType,
  title,
  count,
  filter,
  onFilterChange,
  filterCounts,
  onAdd,
  onPickFromLibrary,
  children,
}: {
  assetType: AssetSheetType;
  title: string;
  count: number;
  filter: GalleryFilter;
  onFilterChange: (filter: GalleryFilter) => void;
  /** Counts matching each filter; all equals `count`. */
  filterCounts: Record<Exclude<GalleryFilter, "all">, number>;
  /** Omitted hides creation, e.g. read-only onboarding demos. */
  onAdd?: () => void;
  /** Omitted hides library selection, e.g. types excluded from global libraries. */
  onPickFromLibrary?: () => void;
  /** Batch-generation entry. */
  children?: ReactNode;
}) {
  const { t } = useTranslation("assets");
  const counts: Record<GalleryFilter, number> = { all: count, ...filterCounts };
  return (
    <div className="sticky top-0 z-sticky flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-background px-5 py-2.5">
      <h2 className="flex items-baseline gap-2 text-base font-semibold">
        {title}
        <span className="text-sm font-normal text-muted-foreground tabular-nums">{count}</span>
      </h2>
      <ToggleGroup
        aria-label={t("sheet_filter_label")}
        variant="outline"
        size="sm"
        value={[filter]}
        onValueChange={(next: string[]) => {
          // Clicking the selected filter again never deselects; exactly one filter remains active.
          if (next[0]) onFilterChange(next[0] as GalleryFilter);
        }}
      >
        {FILTERS.map((value) => (
          <ToggleGroupItem key={value} value={value}>
            {t(`sheet_filter_${value}`)}
            {value !== "all" && <span className="text-muted-foreground tabular-nums">{counts[value]}</span>}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
        {children}
        {onPickFromLibrary && (
          <Button variant="outline" size="sm" onClick={onPickFromLibrary}>
            <Library aria-hidden data-icon="inline-start" />
            {t("from_library")}
          </Button>
        )}
        {onAdd && (
          <Button size="sm" onClick={onAdd}>
            <Plus aria-hidden data-icon="inline-start" />
            {t(`gallery_add.${assetType}`)}
          </Button>
        )}
      </div>
    </div>
  );
}
