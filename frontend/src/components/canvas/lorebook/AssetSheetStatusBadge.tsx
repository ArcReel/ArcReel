import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import type { AssetSheetStatusRow } from "@/types";

/** 资产图过期角标：图照常显示，悬停给出原因。 */
export function AssetSheetStaleBadge({ status }: { status: AssetSheetStatusRow | undefined }) {
  const { t } = useTranslation("assets");
  if (status?.status !== "stale") return null;
  return (
    <span
      className="absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium"
      style={{ background: "oklch(0.24 0.06 60 / 0.9)", color: "oklch(0.86 0.12 75)" }}
      title={t("sheet_status_stale_hint")}
    >
      <AlertTriangle className="h-3 w-3" />
      {t("sheet_status_stale")}
    </span>
  );
}

/** 描述为空时的「缺描述」标记：补上描述之前不能生成资产图。 */
export function MissingDescriptionChip() {
  const { t } = useTranslation("assets");
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[10px] font-medium"
      style={{ background: "oklch(0.24 0.06 60 / 0.55)", color: "oklch(0.86 0.12 75)" }}
      title={t("sheet_description_required")}
    >
      {t("sheet_description_missing")}
    </span>
  );
}

/** 这张资产图是否该当作「还没有图」展示：清单判 missing（含登记了文件却读不到）。 */
export function sheetIsPending(status: AssetSheetStatusRow | undefined): boolean {
  return status?.status === "missing";
}

/** 描述是否可用于生成：去掉两端空白后非空，与服务端判定一致。 */
export function hasUsableDescription(description: string | null | undefined): boolean {
  return typeof description === "string" && description.trim().length > 0;
}
