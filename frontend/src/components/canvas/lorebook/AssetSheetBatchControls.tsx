import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import { AssetSheetBatchDialog } from "./AssetSheetBatchDialog";
import { pendingSheetCounts, type SheetStatusFilter } from "./useAssetSheetStatus";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types";

const FILTERS: SheetStatusFilter[] = ["all", "pending", "stale"];

/**
 * 画廊工具栏上的资产图状态筛选与「生成待生成的 X（N）」批量入口；
 * 旁边注明另有几个因缺描述不会生成。
 */
export function AssetSheetBatchControls({
  projectName,
  assetType,
  rows,
  filter,
  onFilterChange,
  readOnly = false,
}: {
  projectName: string;
  assetType: AssetSheetType;
  rows: AssetSheetStatusRow[];
  filter: SheetStatusFilter;
  onFilterChange: (filter: SheetStatusFilter) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation("assets");
  const [open, setOpen] = useState(false);
  const { generatable, missingDescription } = pendingSheetCounts(rows, assetType);

  return (
    <div className="flex items-center gap-2">
      <div
        role="group"
        aria-label={t("sheet_filter_label")}
        className="inline-flex overflow-hidden rounded-md"
        style={{ border: "1px solid var(--color-hairline)" }}
      >
        {FILTERS.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={filter === value}
            onClick={() => onFilterChange(value)}
            className="focus-ring px-2 py-1 text-[11px] transition-colors"
            style={{
              color: filter === value ? "var(--color-text)" : "var(--color-text-3)",
              background: filter === value ? "var(--color-accent-dim)" : "transparent",
            }}
          >
            {t(`sheet_filter_${value}`)}
          </button>
        ))}
      </div>
      {!readOnly && generatable > 0 && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors"
          style={{
            color: "var(--color-text-2)",
            border: "1px solid var(--color-accent-soft)",
            background: "var(--color-accent-dim)",
          }}
        >
          <Sparkles className="h-3.5 w-3.5" />
          {t(`sheet_batch_button.${assetType}`, { count: generatable })}
        </button>
      )}
      {!readOnly && missingDescription > 0 && (
        <span className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {t("sheet_batch_missing_description", { count: missingDescription })}
        </span>
      )}
      {open && (
        <AssetSheetBatchDialog
          projectName={projectName}
          scope={{ asset_type: assetType }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}
