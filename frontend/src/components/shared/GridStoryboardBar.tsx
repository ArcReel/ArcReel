import { useId } from "react";
import { LayoutGrid } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PillSwitch } from "@/components/legacy/PillSwitch";

export interface GridStoryboardBarProps {
  checked: boolean;
  onToggle: (next: boolean) => void;
  /** 随上方生成模式选择滑入时置 true；常驻呈现（设置页）留空。 */
  animated?: boolean;
}

/**
 * 多宫格分镜装配条。
 *
 * 结构上独立于生成模式卡：宫格只改变分镜图的生产方式，不改变喂给视频模型的输入契约，
 * 因此是分镜图生视频内的选项而非第三种生成模式。向导与设置页共用同一文案与同一开关语义。
 */
export function GridStoryboardBar({ checked, onToggle, animated }: GridStoryboardBarProps) {
  const { t } = useTranslation("dashboard");
  const reactId = useId();
  const labelId = `${reactId}-grid-label`;
  const descId = `${reactId}-grid-desc`;

  return (
    <div
      className={
        "flex items-start gap-2.5 rounded-lg border border-border/50 bg-card/50 px-3.5 py-2.5" +
        (animated ? " arc-slide-in" : "")
      }
    >
      <LayoutGrid
        aria-hidden
        className={`mt-[2px] h-3.5 w-3.5 shrink-0 ${checked ? "text-primary" : "text-muted-foreground"}`}
      />
      <div className="min-w-0 flex-1">
        <div id={labelId} className="text-[11.5px] font-medium text-subtle-foreground">
          {t("grid_storyboard_label")}
        </div>
        <div id={descId} className="mt-0.5 text-[10.5px] leading-[1.5] text-muted-foreground">
          {t("grid_storyboard_desc")}
        </div>
      </div>
      <PillSwitch
        checked={checked}
        onToggle={() => onToggle(!checked)}
        labelledBy={labelId}
        describedBy={descId}
      />
    </div>
  );
}
