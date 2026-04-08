import { useState } from "react";

// ---------------------------------------------------------------------------
// ThinkingBlock – collapsible display of Claude's thinking / reasoning.
// ---------------------------------------------------------------------------

interface ThinkingBlockProps {
  thinking?: string;
}

export function ThinkingBlock({ thinking }: ThinkingBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!thinking) {
    return null;
  }

  return (
    <div className="my-2 rounded-lg border border-purple-500/20 bg-purple-500/5 overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-purple-500/10 transition-colors"
      >
        <span className="text-xs font-medium text-purple-400">
          Thinking
        </span>
        <span className="text-xs text-slate-500">
          {isExpanded ? "\u25BC" : "\u25B6"}
        </span>
      </button>
      {isExpanded && (
        <div className="px-3 py-2 border-t border-purple-500/10">
          <p className="text-xs text-slate-400 italic whitespace-pre-wrap">
            {thinking}
          </p>
        </div>
      )}
    </div>
  );
}
