import { useTranslation } from "react-i18next";
import type { ReferenceUnitCapability } from "@/types";
import { formatNameList } from "@/utils/list-format";
import { bucketLabel } from "./unit-tier-problem";

/**
 * Structured alert for divergence between declared references and available images: identify missing/unregistered references and bucket changes individually.
 * The server already selected by available images; execution blocks on the same issues. Shared by canvas/content confirmation, without silently switching buckets.
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
