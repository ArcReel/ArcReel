import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { EndpointDuplicateDescriptor } from "@/types";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

const ROW_CLS =
  "flex items-center gap-3 rounded-[8px] border border-hairline bg-bg-grad-a/40 px-3 py-2 text-[12.5px] text-text-2";

type EndpointDuplicateChoicesProps = {
  duplicates: EndpointDuplicateDescriptor[];
  disabled: boolean;
} & (
  | {
      /** 导入：每行一个「覆盖」按钮，点击即执行。 */
      onOverwrite: (id: number) => void;
      selection?: undefined;
      blockedSources?: undefined;
    }
  | {
      onOverwrite?: undefined;
      /** 市场安装：单选覆盖目标或新建副本（`null`），由调用方统一确认。 */
      selection: { value: number | null; onChange: (id: number | null) => void };
      /** 已持有安装记录的端点 id → 来源名；这些端点只展示来源，不可选。 */
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

  const version = (dup: EndpointDuplicateDescriptor) => <span className="ml-2 text-text-3">v{dup.version}</span>;
  const relation = (dup: EndpointDuplicateDescriptor) => (
    <span className="shrink-0 text-[11.5px] text-text-3">{t(`ce_import_relation_${dup.relation}`)}</span>
  );

  return (
    <fieldset className="mt-4" disabled={disabled}>
      <legend className="text-[12.5px] text-text-2">{t("ce_import_duplicates")}</legend>
      <div className="mt-2 space-y-2">
        {duplicates.map((dup) => {
          if (!selection) {
            return (
              <div key={dup.id} className={ROW_CLS}>
                <span className="min-w-0 flex-1 truncate">
                  {dup.display_name}
                  {version(dup)}
                </span>
                {relation(dup)}
                <button type="button" disabled={disabled} onClick={() => onOverwrite(dup.id)} className={GHOST_BTN_CLS}>
                  {t("ce_import_overwrite")}
                </button>
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
            <label key={dup.id} className={`${ROW_CLS} flex-wrap`}>
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
