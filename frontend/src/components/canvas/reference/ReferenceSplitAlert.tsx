import { useTranslation } from "react-i18next";
import type { ReferenceUnitCapability } from "@/types";
import { formatNameList } from "@/utils/list-format";
import { bucketLabel } from "./unit-tier-problem";

/**
 * 声明引用与可用参考图分裂的结构化提示：逐条点名缺图 / 未登记的引用与桶的改变。服务端已按
 * 可用图定桶，执行侧会以同样的问题阻断；画布与内容确认面板共用这一份，不静默换桶。
 */
export function ReferenceSplitAlert({
  capability,
  className,
}: {
  capability: ReferenceUnitCapability;
  className: string;
}) {
  const { t, i18n } = useTranslation("dashboard");
  return (
    <div role="alert" data-testid="reference-split-alert" className={className}>
      <p className="font-medium">{t("reference_unit_split_title")}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        {capability.unavailable_references.length > 0 && (
          <li>
            {t("reference_unit_unavailable_references", {
              names: formatNameList(
                capability.unavailable_references.map((ref) => ref.name),
                i18n.language,
              ),
            })}
          </li>
        )}
        {capability.unregistered_references.length > 0 && (
          <li>
            {t("reference_unit_unregistered_references", {
              names: formatNameList(capability.unregistered_references, i18n.language),
            })}
          </li>
        )}
        {capability.declared_capability !== capability.hydrated_capability && (
          <li>
            {t("reference_unit_bucket_changed", {
              declared: bucketLabel(t, capability.declared_capability),
              hydrated: bucketLabel(t, capability.hydrated_capability),
            })}
          </li>
        )}
      </ul>
    </div>
  );
}
