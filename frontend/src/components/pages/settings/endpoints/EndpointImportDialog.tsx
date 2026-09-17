import { useId } from "react";
import { CircleAlert, Loader2, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GlassModal } from "@/components/ui/GlassModal";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type { EndpointDefinition, EndpointValidateResponse } from "@/types";
import { formatNameList } from "@/utils/list-format";
import { EndpointDuplicateChoices } from "./EndpointDuplicateChoices";

/** 导入确认：先看校验结果与同作者同名定义，再决定新建副本、覆盖既有，还是取消。 */
export function EndpointImportDialog({
  open,
  fileName,
  definition,
  validation,
  busy,
  onCreateCopy,
  onOverwrite,
  onCancel,
}: {
  open: boolean;
  fileName: string;
  definition: EndpointDefinition | null;
  validation: EndpointValidateResponse | null;
  busy: boolean;
  onCreateCopy: () => void;
  onOverwrite: (id: number) => void;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const titleId = useId();

  const hasErrors = (validation?.errors.length ?? 0) > 0;
  const schemaVersion = validation?.schema_version;
  const minAppVersion = validation?.min_app_version;

  return (
    <GlassModal
      open={open}
      onClose={onCancel}
      labelledBy={titleId}
      widthClassName="w-full max-w-2xl"
      panelClassName="max-h-[80vh] overflow-y-auto"
    >
      <div className="p-5">
        <h2 id={titleId} className="font-editorial text-[18px] text-text">
          {t("ce_import_title")}
        </h2>
        <p className="mt-1 text-[12px] text-text-3">
          {fileName}
          {definition?.meta?.name ? ` · ${definition.meta.name}` : ""}
          {definition?.meta?.version ? ` · v${definition.meta.version}` : ""}
        </p>

        {validation === null && (
          <p className="mt-3 flex items-center gap-2 text-[12px] text-text-3">
            <Loader2 className="h-3 w-3 motion-safe:animate-spin text-accent-2" aria-hidden />
            {t("common:loading")}
          </p>
        )}

        {schemaVersion && schemaVersion.level !== "direct" && (
          <p className="mt-3 rounded-[8px] border border-hairline bg-bg-grad-a/40 px-3 py-2 text-[12px] leading-[1.55] text-text-2">
            {t("ce_import_schema_mismatch", {
              file: schemaVersion.file ?? t("ce_import_schema_unknown"),
              current: schemaVersion.current,
            })}
          </p>
        )}

        {minAppVersion && !minAppVersion.satisfied && (
          <p className="mt-3 flex items-start gap-2 rounded-[8px] border border-hairline bg-bg-grad-a/40 px-3 py-2 text-[12px] leading-[1.55] text-text-2">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warm-bright" aria-hidden />
            <span>
              {t("ce_import_requires_newer_app", {
                required: minAppVersion.required,
                current: minAppVersion.current,
              })}
            </span>
          </p>
        )}

        {validation && (validation.errors.length > 0 || validation.warnings.length > 0) && (
          <div className="mt-3 space-y-1.5">
            {validation.errors.map((issue) => (
              <div key={`e-${issue.path}-${issue.code}`} className="flex items-start gap-2">
                <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warm-bright" aria-hidden />
                <span className="text-[12px] leading-[1.55] text-text-2">{issue.message}</span>
              </div>
            ))}
            {validation.warnings.map((issue) => (
              <div key={`w-${issue.path}-${issue.code}`} className="flex items-start gap-2">
                <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-3" aria-hidden />
                <span className="text-[12px] leading-[1.55] text-text-2">{issue.message}</span>
              </div>
            ))}
          </div>
        )}

        {validation?.hints?.base_url && (
          <p className="mt-3 text-[12px] text-text-3">
            {t("ce_import_hint_base_url", { url: validation.hints.base_url })}
          </p>
        )}
        {validation?.hints?.suggested_models && validation.hints.suggested_models.length > 0 && (
          <p className="mt-1 text-[12px] text-text-3">
            {t("ce_import_hint_models", {
              models: formatNameList(
                validation.hints.suggested_models.map((m) => m.label ?? m.id),
                i18n.language,
              ),
            })}
          </p>
        )}

        {validation && (
          <EndpointDuplicateChoices
            duplicates={validation.duplicates}
            disabled={busy || hasErrors}
            onOverwrite={onOverwrite}
          />
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className={GHOST_BTN_CLS}>
            {t("common:cancel")}
          </button>
          <button
            type="button"
            disabled={busy || hasErrors || !definition || validation === null}
            onClick={onCreateCopy}
            className={ACCENT_BTN_SM_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {validation && validation.duplicates.length > 0
              ? t("ce_import_create_copy")
              : t("ce_import_create")}
          </button>
        </div>
        {hasErrors && (
          <p className="mt-2 text-right text-[12px] text-warm-bright">{t("ce_import_blocked")}</p>
        )}
      </div>
    </GlassModal>
  );
}
