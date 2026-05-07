import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

// ---------------------------------------------------------------------------
// ThinkingBlock – collapsible display of Claude's thinking / reasoning.
// ---------------------------------------------------------------------------

interface ThinkingBlockProps {
  thinking?: string;
}

export function ThinkingBlock({ thinking }: ThinkingBlockProps) {
  const { t } = useTranslation("dashboard");
  const [isExpanded, setIsExpanded] = useState(false);
  const detailsId = useId();

  if (!thinking) {
    return null;
  }

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
        aria-controls={detailsId}
        className="flex w-full items-center justify-between px-3 py-2 text-left transition-colors"
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.76 0.09 295 / 0.18)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
      >
        <span
          className="text-[11.5px] font-medium uppercase tracking-wide"
          style={{ color: "var(--color-accent-2)" }}
        >
          {t("thinking_process_label")}
        </span>
        <span
          className="text-[11px]"
          style={{ color: "var(--color-text-4)" }}
        >
          {isExpanded ? "▼" : "▶"}
        </span>
      </button>
      {isExpanded && (
        <div
          id={detailsId}
          className="px-3 py-2"
          style={{ borderTop: "1px solid var(--color-accent-soft)" }}
        >
          <p
            className="whitespace-pre-wrap text-[11.5px] italic leading-[1.55]"
            style={{ color: "var(--color-text-3)" }}
          >
            {thinking}
          </p>
        </div>
      )}
    </div>
  );
}
