import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { EndpointDuplicateDescriptor } from "@/types";
import { Button } from "@/components/ui/button";

const ROW_CLS =
  "flex items-center gap-3 rounded-lg border border-border px-3 py-2 text-sm text-subtle-foreground";

type EndpointDuplicateChoicesProps = {
  duplicates: EndpointDuplicateDescriptor[];
  disabled: boolean;
} & (
  | {
      /** Import: one Overwrite button per row, executing immediately on click. */
      onOverwrite: (id: number) => void;
      selection?: undefined;
      blockedSources?: undefined;
    }
  | {
      onOverwrite?: undefined;
      /** Market installation: select one overwrite target or a new copy (`null`); the caller handles overall confirmation. */
      selection: { value: number | null; onChange: (id: number | null) => void };
      /** Endpoint IDs with existing installation records → source names; show their source only and make them unselectable. */
      blockedSources: Record<number, string>;
    }
);

export function EndpointDuplicateChoices({
  duplicates,
  disabled,
  onOverwrite,
  selection,
  blockedSources,
}: EndpointDuplicateChoicesProps) {
  const { t } = useTranslation("dashboard");
  const groupName = useId();
  if (duplicates.length === 0) return null;

  const version = (dup: EndpointDuplicateDescriptor) => <span className="ml-2 text-muted-foreground">v{dup.version}</span>;
  const relation = (dup: EndpointDuplicateDescriptor) => (
    <span className="shrink-0 text-xs text-muted-foreground">{t(`ce_import_relation_${dup.relation}`)}</span>
  );

  return (
    <fieldset className="mt-4" disabled={disabled}>
      <legend className="text-sm font-medium">{t("ce_import_duplicates")}</legend>
      <div className="mt-2 flex flex-col gap-2">
        {duplicates.map((dup) => {
          if (!selection) {
            return (
              <div key={dup.id} className={ROW_CLS}>
                <span className="min-w-0 flex-1 truncate">
                  {dup.display_name}
                  {version(dup)}
                </span>
                {relation(dup)}
                <Button variant="outline" size="sm" disabled={disabled} onClick={() => onOverwrite(dup.id)}>
                  {t("ce_import_overwrite")}
                </Button>
              </div>
            );
          }
          const blockedSource = blockedSources[dup.id];
          if (blockedSource !== undefined) {
            return (
              <div key={dup.id} className={`${ROW_CLS} flex-wrap`}>
                <span className="min-w-0 flex-1">
                  {dup.display_name}
                  {version(dup)}
                </span>
                {relation(dup)}
                <span>{t("market_installed_from", { source: blockedSource })}</span>
              </div>
            );
          }
          return (
            <label key={dup.id} className={`${ROW_CLS} flex-wrap has-focus-visible:ring-3 has-focus-visible:ring-ring/50`}>
              <input
                type="radio"
                name={groupName}
                aria-label={`${t("ce_import_overwrite")} ${dup.display_name}`}
                disabled={disabled}
                checked={selection.value === dup.id}
                onChange={() => selection.onChange(dup.id)}
              />
              <span className="min-w-0 flex-1">
                {t("ce_import_overwrite")} · {dup.display_name}
                {version(dup)}
              </span>
              {relation(dup)}
            </label>
          );
        })}
        {selection && (
          <label className={ROW_CLS}>
            <input
              type="radio"
              name={groupName}
              disabled={disabled}
              checked={selection.value === null}
              onChange={() => selection.onChange(null)}
            />
            {t("market_create_copy")}
          </label>
        )}
      </div>
    </fieldset>
  );
}
