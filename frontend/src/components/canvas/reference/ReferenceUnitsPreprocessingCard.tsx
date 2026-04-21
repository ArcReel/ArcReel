import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";
import { PreprocessingView } from "@/components/canvas/timeline/PreprocessingView";

export interface ReferenceUnitsPreprocessingCardProps {
  projectName: string;
  episode: number;
  unitCount: number;
}

// 折叠态：chevron + 状态点 + "Units 拆分已完成 (N units)" 一行简报，整体为 inline-flex 按钮。
// 展开态：仅 chevron + "收起"，避免与 PreprocessingView 内的 statusLabel 重复。
export function ReferenceUnitsPreprocessingCard({
  projectName,
  episode,
  unitCount,
}: ReferenceUnitsPreprocessingCardProps) {
  const { t } = useTranslation("dashboard");
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-gray-800 px-4 py-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="inline-flex items-center gap-2 rounded px-1 py-0.5 text-xs text-gray-400 hover:text-gray-200 focus-ring"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
        )}
        {expanded ? (
          <span>{t("reference_preproc_collapse")}</span>
        ) : (
          <>
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
            <span>{t("reference_units_split_complete", { count: unitCount })}</span>
          </>
        )}
      </button>
      {expanded && (
        <div className="mt-3">
          <PreprocessingView projectName={projectName} episode={episode} contentMode="reference_video" />
        </div>
      )}
    </div>
  );
}
