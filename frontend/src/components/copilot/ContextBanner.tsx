import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { X, User, MapPin, Puzzle, Film } from "lucide-react";

/**
 * ContextBanner — Two stacked sub-banners:
 * 1. FocusedContextBanner: shows when a character/clue/segment is "in focus"
 * 2. ContextLengthBanner: warns when conversation is approaching model context limit
 */
export function ContextBanner() {
  return (
    <>
      <FocusedContextBanner />
      <ContextLengthBanner />
    </>
  );
}

function FocusedContextBanner() {
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

function ContextLengthBanner() {
  const { contextLong, turnsCount } = useAssistantStore();
  const [dismissed, setDismissed] = useState(false);

  // Auto-reset dismissed when context_long flips off (new session)
  useEffect(() => {
    if (!contextLong) setDismissed(false);
  }, [contextLong]);

  if (!contextLong || dismissed) return null;

  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-2 border-b border-amber-500/20 bg-amber-500/10 px-3 py-1.5"
    >
      <p className="text-[11px] leading-snug text-amber-400/90">
        <span className="mr-1">⚡</span>
        Long conversation ({turnsCount} turns) — context nearing model limit.
        Consider starting a{" "}
        <span className="font-medium text-amber-300">new session</span>.
      </p>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="shrink-0 rounded px-1 text-[10px] text-amber-500/60 transition-colors hover:text-amber-400"
        title="Dismiss"
        aria-label="Dismiss context warning"
      >
        ✕
      </button>
    </div>
  );
}
