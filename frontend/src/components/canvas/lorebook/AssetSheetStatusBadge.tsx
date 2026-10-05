import { useTranslation } from "react-i18next";

/** 描述为空时的「缺描述」标记：补上描述之前不能生成资产图。 */
export function MissingDescriptionChip() {
  const { t } = useTranslation("assets");
  return (
    <span
      className="rounded-sm px-1.5 py-0.5 text-[10px] font-medium"
      style={{ background: "oklch(0.24 0.06 60 / 0.55)", color: "oklch(0.86 0.12 75)" }}
      title={t("sheet_description_required")}
    >
      {t("sheet_description_missing")}
    </span>
  );
}

/** 描述是否可用于生成：去掉两端空白后非空，与服务端判定一致。 */
export function hasUsableDescription(description: string | null | undefined): boolean {
  return typeof description === "string" && description.trim().length > 0;
}
