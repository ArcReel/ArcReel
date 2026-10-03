import { useEffect, useId, useState } from "react";
import { RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { CARD_STYLE, GHOST_BTN_LG_CLS } from "@/components/shared/darkroom-tokens";
import { PillSwitch } from "@/components/legacy/PillSwitch";
import { useAppStore } from "@/stores/app-store";
import type { OfficialServiceState } from "@/types";
import { errMsg } from "@/utils/async";

/**
 * 官方服务的总开关与实例标识重置，即时生效。发行版未配置官方服务地址时只说明处于关闭状态。
 * 放在「关于」而不是市场页：关闭后市场页不再展示任何官方服务元素，重新开启的入口须在别处。
 */
export function OfficialServiceCard() {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const toggleLabelId = useId();
  const descId = useId();
  const [state, setState] = useState<OfficialServiceState | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingReset, setConfirmingReset] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    API.getOfficialService({ signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setState(result);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          pushToast(t("official_service_update_failed", { message: errMsg(err) }), "error");
        }
      });
    return () => controller.abort();
  }, [pushToast, t]);

  const run = async (action: () => Promise<OfficialServiceState>, done?: string) => {
    setBusy(true);
    try {
      setState(await action());
      if (done) pushToast(done, "success");
    } catch (err) {
      pushToast(t("official_service_update_failed", { message: errMsg(err) }), "error");
    } finally {
      setBusy(false);
    }
  };

  if (state === null) return null;

  return (
    <div className="rounded-[12px] border border-hairline p-6" style={CARD_STYLE}>
      <div className="mb-3 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
        {t("official_service_title")}
      </div>
      <p id={descId} className="max-w-[72ch] text-[12.5px] text-text-3">
        {state.available ? t("official_service_desc") : t("official_service_not_configured")}
      </p>
      {state.available && (
        <>
          <div className="mt-4 flex items-center gap-3 text-[12.5px] text-text-2">
            <span id={toggleLabelId}>{t("official_service_toggle")}</span>
            <PillSwitch
              checked={state.enabled}
              disabled={busy}
              labelledBy={toggleLabelId}
              describedBy={descId}
              onToggle={() => void run(() => API.updateOfficialService({ enabled: !state.enabled }))}
            />
          </div>
          <dl className="mt-4 flex flex-wrap items-baseline gap-x-2 text-[12px]">
            <dt className="text-text-3">{t("official_service_instance_id")}</dt>
            <dd className="break-all font-mono text-text-2">
              {state.instance_id ?? t("official_service_instance_id_none")}
            </dd>
          </dl>
          <button
            type="button"
            disabled={busy}
            onClick={() => setConfirmingReset(true)}
            className={`${GHOST_BTN_LG_CLS} mt-3`}
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            {t("official_service_reset")}
          </button>
        </>
      )}
      <ConfirmDialog
        open={confirmingReset}
        title={t("official_service_reset")}
        description={t("official_service_reset_desc")}
        confirmLabel={t("official_service_reset_confirm")}
        tone="danger"
        loading={busy}
        onCancel={() => setConfirmingReset(false)}
        onConfirm={async () => {
          await run(() => API.resetOfficialInstanceId(), t("official_service_reset_done"));
          setConfirmingReset(false);
        }}
      />
    </div>
  );
}
