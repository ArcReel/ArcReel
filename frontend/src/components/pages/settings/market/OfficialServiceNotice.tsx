import { useId } from "react";
import { Info } from "lucide-react";
import { Link } from "wouter";
import { Trans, useTranslation } from "react-i18next";
import { marketSettingsPath } from "@/app-routes";
import { Button } from "@/components/ui/button";

/**
 * 官方服务开启后首次进入市场的说明：上报了什么、在哪里关闭（链接到市场「设置」）；确认或关闭后不再出现。
 */
export function OfficialServiceNotice({
  busy,
  onAcknowledge,
  onTurnOff,
}: {
  busy: boolean;
  onAcknowledge: () => void;
  onTurnOff: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm"
    >
      <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
      <div className="flex min-w-0 flex-col gap-1">
        <h3 id={titleId} className="font-medium">
          {t("official_notice_title")}
        </h3>
        <p className="max-w-prose text-subtle-foreground">
          <Trans
            t={t}
            i18nKey="official_notice_body"
            components={{
              settings: (
                <Link href={marketSettingsPath("settings")} className="text-primary underline underline-offset-4" />
              ),
            }}
          />
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={onAcknowledge}>
            {t("official_notice_ack")}
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={onTurnOff}>
            {t("official_notice_turn_off")}
          </Button>
        </div>
      </div>
    </section>
  );
}
