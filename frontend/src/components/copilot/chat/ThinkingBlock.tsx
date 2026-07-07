import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

// ---------------------------------------------------------------------------
// ThinkingBlock – single-line, low-contrast display of Claude's reasoning.
//
// 工序行视觉语言：无边框无底色。流式期间为动态「思考中…」单行条；
// 完成后收敛为首行摘要，点击展开全文（左侧 rail + 缩进）。
// ---------------------------------------------------------------------------

interface ThinkingBlockProps {
  thinking?: string;
  /** 该块正在流式生成（属于 draft turn 的末尾块）。 */
  streaming?: boolean;
}

function firstLine(text: string): string {
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed) return trimmed;
  }
  return "";
}

export function ThinkingBlock({ thinking, streaming }: ThinkingBlockProps) {
  const { t } = useTranslation("dashboard");
  const [isExpanded, setIsExpanded] = useState(false);
  const detailsId = useId();

  if (streaming) {
    return (
      <div
        className="my-1 flex items-center gap-1.5 text-[11.5px] motion-safe:animate-pulse"
        style={{ color: "var(--color-text-4)" }}
      >
        <span aria-hidden="true">{"✳"}</span>
        <span>{t("thinking_streaming")}</span>
      </div>
    );
  }

  if (!thinking?.trim()) {
    return null;
  }

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        aria-expanded={isExpanded}
        aria-controls={detailsId}
        aria-label={t("thinking_process_label")}
        className="flex w-full min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors"
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(1 0 0 / 0.04)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
      >
        <span aria-hidden="true" className="shrink-0 text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
          {"✳"}
        </span>
        <span className="min-w-0 flex-1 truncate text-[11.5px] italic" style={{ color: "var(--color-text-3)" }}>
          {firstLine(thinking)}
        </span>
        <span className="shrink-0 text-[10px]" style={{ color: "var(--color-text-4)" }}>
          {isExpanded ? "▼" : "▶"}
        </span>
      </button>
      {isExpanded && (
        <div
          id={detailsId}
          className="ml-1.5 mt-1 pl-2.5"
          style={{ borderLeft: "2px solid var(--color-accent-soft)" }}
        >
          <p className="whitespace-pre-wrap text-[11.5px] italic leading-[1.55]" style={{ color: "var(--color-text-3)" }}>
            {thinking}
          </p>
        </div>
      )}
    </div>
  );
}
