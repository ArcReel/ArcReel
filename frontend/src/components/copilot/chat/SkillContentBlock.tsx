import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("dashboard");
  const [isExpanded, setIsExpanded] = useState(false);
  const panelId = useId();

  if (!text) {
    return null;
  }

  const skillName = extractSkillName(text);

  return (
    <div
      className="my-2 overflow-hidden rounded-lg"
      style={{
        border: "1px solid var(--color-accent-soft)",
        background: "var(--color-accent-dim)",
      }}
    >
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        aria-expanded={isExpanded}
        aria-controls={panelId}
        className="flex w-full items-center justify-between px-3 py-2 text-left transition-colors"
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.76 0.09 295 / 0.18)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
      >
        <div className="flex items-center gap-2">
          <span
            className="text-[11.5px] font-semibold uppercase tracking-wide"
            style={{ color: "var(--color-accent-2)" }}
          >
            {t("skill_content_label")}
          </span>
          <span
            className="num text-[11px]"
            style={{ color: "var(--color-text-3)" }}
          >
            {skillName}
          </span>
        </div>
        <span
          className="text-[11px]"
          style={{ color: "var(--color-text-4)" }}
        >
          {isExpanded ? t("skill_content_collapse") : t("skill_content_expand")}
        </span>
      </button>
      {isExpanded && (
        <div
          id={panelId}
          className="max-h-96 overflow-y-auto px-3 py-2"
          style={{
            borderTop: "1px solid var(--color-accent-soft)",
            background: "oklch(0.76 0.09 295 / 0.06)",
          }}
        >
          <StreamMarkdown content={text} />
        </div>
      )}
    </div>
  );
}
