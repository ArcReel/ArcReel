import { useId } from "react";
import { useTranslation } from "react-i18next";
import { GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";

/** 官方服务开启后首次进入市场页的说明：上报了什么、在哪里关闭；确认或关闭后不再出现。 */
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
      className="mb-5 rounded-[10px] border border-accent/30 bg-accent-dim px-4 py-3 text-[12.5px] leading-[1.6] text-text-2"
    >
      <h3 id={titleId} className="font-medium text-text">
        {t("official_notice_title")}
      </h3>
      <p className="mt-1 max-w-[72ch]">{t("official_notice_body")}</p>
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <button type="button" className={GHOST_BTN_CLS} disabled={busy} onClick={onAcknowledge}>
          {t("official_notice_ack")}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onTurnOff}
          className="text-[12px] text-text-3 underline-offset-2 hover:text-text hover:underline disabled:opacity-40"
        >
          {t("official_notice_turn_off")}
        </button>
      </div>
    </section>
  );
}
