import { useAppStore } from "@/stores/app-store";
import { X, User, MapPin, Puzzle, Film } from "lucide-react";

export function ContextBanner() {
  const { focusedContext, setFocusedContext } = useAppStore();

  if (!focusedContext) return null;

  const icons = { character: User, scene: MapPin, prop: Puzzle, segment: Film };
  const Icon = icons[focusedContext.type];
  const labels: Record<string, string> = { character: "角色", scene: "场景", prop: "道具", segment: "片段" };

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
      <span style={{ color: "var(--color-text-3)" }}>{labels[focusedContext.type]}:</span>
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
        aria-label="清除上下文"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
