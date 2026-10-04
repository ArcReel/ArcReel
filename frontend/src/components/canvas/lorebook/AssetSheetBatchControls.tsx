import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AssetSheetBatchDialog } from "./AssetSheetBatchDialog";
import { pendingSheetCounts } from "./useAssetSheetStatus";
import { useActiveResourceIds } from "@/stores/tasks-store";
import type { AssetSheetStatusRow, AssetSheetType } from "@/types";

/**
 * 画廊工具栏上的「生成待生成的 X（N）」批量入口；旁边注明另有几个因缺描述不会生成。
 */
export function AssetSheetBatchControls({
  projectName,
  assetType,
  rows,
}: {
  projectName: string;
  assetType: AssetSheetType;
  rows: AssetSheetStatusRow[];
}) {
  const { t } = useTranslation("assets");
  const [open, setOpen] = useState(false);
  const activeOwners = useActiveResourceIds(assetType, projectName);
  const activeDerivatives = useActiveResourceIds("character_derivative", projectName);
  const { generatable, missingDescription } = pendingSheetCounts(rows, assetType, (row) =>
    row.derivative === null ? activeOwners.has(row.name) : activeDerivatives.has(`${row.name}/${row.derivative}`),
  );

  return (
    <>
      {missingDescription > 0 && (
        <span className="text-xs text-muted-foreground">
          {t("sheet_batch_missing_description", { count: missingDescription })}
        </span>
      )}
      {generatable > 0 && (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          <Sparkles aria-hidden data-icon="inline-start" />
          {t(`sheet_batch_button.${assetType}`, { count: generatable })}
        </Button>
      )}
      {open && (
        <AssetSheetBatchDialog
          projectName={projectName}
          scope={{ asset_type: assetType }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
