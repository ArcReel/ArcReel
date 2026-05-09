import type { CSSProperties } from "react";
import { AlertTriangle, Loader2, Save } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";

interface TabSaveFooterProps {
  isDirty: boolean;
  saving: boolean;
  disabled?: boolean;
  error: string | null;
  onSave: () => void;
  onReset: () => void;
}

const FOOTER_DIRTY_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.65), oklch(0.15 0.010 265 / 0.55))",
  backdropFilter: "blur(28px) saturate(1.5)",
  WebkitBackdropFilter: "blur(28px) saturate(1.5)",
  borderTop: "1px solid var(--color-hairline)",
  boxShadow: "0 -8px 24px -12px oklch(0 0 0 / 0.45)",
};

export function TabSaveFooter({
  isDirty,
  saving,
  disabled = false,
  error,
  onSave,
  onReset,
}: TabSaveFooterProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const controlsDisabled = saving || disabled;

  return (
    <div
      className={
        "flex items-center justify-between px-5 py-3" +
        (isDirty ? " sticky bottom-0 z-10" : "")
      }
      style={isDirty ? FOOTER_DIRTY_STYLE : undefined}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        {isDirty && !error && (
          <>
            <span
              aria-hidden
              className="h-1.5 w-1.5 rounded-full"
              style={{
                background: "var(--color-warm)",
                boxShadow: "0 0 8px var(--color-warm-glow)",
              }}
            />
            <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.16em] text-warm-bright">
              {t("common:unsaved")}
            </span>
            <span className="text-[12px] text-text-3">{t("unsaved_changes_hint")}</span>
          </>
        )}
        {error && (
          <div role="alert" className="flex min-w-0 items-center gap-1.5">
            <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0 text-warm" />
            <span className="truncate text-[12px] text-warm-bright">{error}</span>
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {isDirty && (
          <button
            type="button"
            onClick={onReset}
            disabled={controlsDisabled}
            className={GHOST_BTN_LG_CLS}
          >
            {t("common:reset")}
          </button>
        )}
        <button
          type="button"
          onClick={onSave}
          disabled={!isDirty || controlsDisabled}
          className={ACCENT_BTN_CLS}
          style={
            isDirty
              ? ACCENT_BUTTON_STYLE
              : {
                  background: "oklch(0.20 0.010 265 / 0.55)",
                  color: "var(--color-text-4)",
                  border: "1px solid var(--color-hairline-soft)",
                }
          }
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
          ) : (
            <Save className="h-3.5 w-3.5" aria-hidden />
          )}
          {saving ? t("common:saving") : t("common:save")}
        </button>
      </div>
    </div>
  );
}
