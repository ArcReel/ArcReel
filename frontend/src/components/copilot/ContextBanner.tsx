import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { X, User, MapPin, Puzzle, Film } from "lucide-react";

export function ContextBanner() {
  const { t } = useTranslation("dashboard");
  const { focusedContext, setFocusedContext } = useAppStore();

  if (!focusedContext) return null;

  const icons = { character: User, scene: MapPin, prop: Puzzle, segment: Film };
  const Icon = icons[focusedContext.type];
  const labelKey = `context_label_${focusedContext.type}` as const;

  return (
    <div
      className="flex items-center gap-2 px-3 py-1.5 text-[11.5px]"
      style={{
        borderBottom: "1px solid var(--color-hairline-soft)",
        background: "var(--color-accent-dim)",
      }}
    >
      <Icon
        className="h-3.5 w-3.5"
        style={{ color: "var(--color-accent)" }}
      />
      <span style={{ color: "var(--color-text-3)" }}>{t(labelKey)}:</span>
      <span
        className="font-medium"
        style={{ color: "var(--color-accent-2)" }}
      >
        {focusedContext.id}
      </span>
      <button
        onClick={() => setFocusedContext(null)}
        className="ml-auto rounded p-0.5 transition-colors focus-ring"
        style={{ color: "var(--color-text-4)" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.28 0.012 265 / 0.5)";
          e.currentTarget.style.color = "var(--color-text)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--color-text-4)";
        }}
        aria-label={t("context_clear")}
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
