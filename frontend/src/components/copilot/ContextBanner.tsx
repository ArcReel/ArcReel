import { useAppStore } from "@/stores/app-store";
import { X, User, Puzzle, Film } from "lucide-react";

export function ContextBanner() {
  const { focusedContext, setFocusedContext } = useAppStore();

  if (!focusedContext) return null;

  const icons = { character: User, clue: Puzzle, segment: Film };
  const Icon = icons[focusedContext.type];
  const labels: Record<string, string> = { character: "Character", clue: "Clue", segment: "Segment" };

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
