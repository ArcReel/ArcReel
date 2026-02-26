import { useState } from "react";
import { cn } from "./utils";
import { StreamMarkdown } from "../StreamMarkdown";

// ---------------------------------------------------------------------------
// SkillContentBlock – renders skill_content blocks (standalone or within a
// Skill tool call).  Shows a collapsible panel with the skill name extracted
// from the text and the full content rendered as markdown.
// ---------------------------------------------------------------------------

interface SkillContentBlockProps {
  text?: string;
}

function extractSkillName(text: string | undefined): string {
  if (!text) return "Skill";

  // Try to extract from "Launching skill: xxx"
  const launchMatch = text.match(/Launching skill:\s*(\S+)/);
  if (launchMatch) return launchMatch[1];

  // Try to extract from path
  const pathMatch = text.match(/\.claude\/skills\/([^/\s]+)/);
  if (pathMatch) return pathMatch[1];

  return "Skill";
}

export function SkillContentBlock({ text }: SkillContentBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!text) {
    return null;
  }

  const skillName = extractSkillName(text);

  return (
    <div className="my-2 rounded-lg border border-purple-400/20 bg-purple-500/5 overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-purple-500/10 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-purple-400">
            Skill 内容
          </span>
          <span className="text-xs text-slate-400">
            {skillName}
          </span>
        </div>
        <span className="text-xs text-slate-500">
          {isExpanded ? "\u25BC 收起" : "\u25B6 展开"}
        </span>
      </button>
      {isExpanded && (
        <div className={cn(
          "px-3 py-2 border-t border-purple-400/10 bg-purple-900/10",
          "max-h-96 overflow-y-auto",
        )}>
          <StreamMarkdown content={text} />
        </div>
      )}
    </div>
  );
}
