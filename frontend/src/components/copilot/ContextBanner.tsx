import { useState, useEffect } from "react";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { X, User, Puzzle, Film } from "lucide-react";

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
  const { focusedContext, setFocusedContext } = useAppStore();

  if (!focusedContext) return null;

  const icons = { character: User, clue: Puzzle, segment: Film };
  const Icon = icons[focusedContext.type];
  const labels: Record<string, string> = { character: "角色", clue: "线索", segment: "片段" };

  return (
    <div className="flex items-center gap-2 border-b border-gray-800 bg-indigo-950/30 px-3 py-1.5 text-xs">
      <Icon className="h-3.5 w-3.5 text-indigo-400" />
      <span className="text-gray-400">{labels[focusedContext.type]}:</span>
      <span className="font-medium text-indigo-300">{focusedContext.id}</span>
      <button
        onClick={() => setFocusedContext(null)}
        className="ml-auto rounded p-0.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300"
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
