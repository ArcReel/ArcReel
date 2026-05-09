import type { CSSProperties } from "react";
import { Loader2, Save } from "lucide-react";
import { useTranslation } from "react-i18next";

interface TabSaveFooterProps {
  isDirty: boolean;
  saving: boolean;
  disabled?: boolean;
  error: string | null;
  onSave: () => void;
  onReset: () => void;
}

const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
  boxShadow:
    "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 6px 18px -8px var(--color-accent-glow)",
};

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
  const { t } = useTranslation("dashboard");
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
              Unsaved
            </span>
            <span className="text-[12px] text-text-3">{t("unsaved_changes_hint")}</span>
          </>
        )}
        {error && (
          <>
            <span aria-hidden className="text-warm">▲</span>
            <span className="truncate text-[12px] text-warm-bright">{error}</span>
          </>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {isDirty && (
          <button
            type="button"
            onClick={onReset}
            disabled={controlsDisabled}
            className="inline-flex items-center gap-2 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3.5 py-2 text-[12px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
          >
            {t("common:reset")}
          </button>
        )}
        <button
          type="button"
          onClick={onSave}
          disabled={!isDirty || controlsDisabled}
          className={
            "inline-flex items-center gap-2 rounded-[8px] px-4 py-2 text-[12.5px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
          }
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
