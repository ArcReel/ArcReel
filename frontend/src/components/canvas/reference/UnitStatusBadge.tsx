import { useTranslation } from "react-i18next";
import type { UnitStatus } from "@/types";
import { STATUS_CONF } from "./unit-status";

/** 视频单元的状态徽标。 */
export function UnitStatusBadge({ status }: { status: UnitStatus }) {
  const { t } = useTranslation("dashboard");
  const conf = STATUS_CONF[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs font-medium ${conf.badgeClass}`}>
      <span aria-hidden="true" className={`size-1.5 rounded-full ${conf.dotClass}`} />
      {t(conf.i18nKey)}
    </span>
  );
}
