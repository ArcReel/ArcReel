import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MarketInstallationState } from "@/types";

const BADGE_CLS =
  "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-[5px] border px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em]";

const TONE_CLS = {
  good: "border-good/35 bg-good/10 text-good",
  accent: "border-accent/35 bg-accent-dim text-accent-2",
  warn: "border-warn/40 bg-warn/10 text-warn",
  muted: "border-hairline-soft bg-bg-grad-a/55 text-text-3",
} as const;

function Badge({ tone, children }: { tone: keyof typeof TONE_CLS; children: ReactNode }) {
  return <span className={`${BADGE_CLS} ${TONE_CLS[tone]}`}>{children}</span>;
}

/** 已安装端点的两轴徽标：市场轴（已安装 / 可更新 / 市场中不可用）× 本地修改轴（已修改）。 */
export function MarketInstallBadges({ state, modified }: { state: MarketInstallationState; modified: boolean }) {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {state === "current" && (
        <Badge tone="good">
          <Check className="h-2.5 w-2.5" aria-hidden />
          {t("market_installed")}
        </Badge>
      )}
      {state === "update_available" && <Badge tone="accent">{t("market_update_available")}</Badge>}
      {state === "unavailable" && <Badge tone="warn">{t("market_unavailable")}</Badge>}
      {modified && <Badge tone="muted">{t("market_modified")}</Badge>}
    </span>
  );
}
