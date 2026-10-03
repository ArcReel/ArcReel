import { useTranslation } from "react-i18next";

/**
 * 集规划状态为 stale 的标记「原文已重新规划」。
 *
 * 与产物过期（暖色实底）区分：青色虚线框，只说这一集的原文动过，不说产物旧了。
 */
export function ReplannedBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span
      className="inline-flex items-center rounded-sm px-1.5 py-px text-[10.5px] leading-[1.6]"
      style={{ color: "oklch(0.85 0.08 200)", border: "1px dashed oklch(0.85 0.08 200 / 0.6)" }}
      title={t("episodes_view_replanned_hint")}
    >
      {t("episodes_view_replanned")}
    </span>
  );
}
