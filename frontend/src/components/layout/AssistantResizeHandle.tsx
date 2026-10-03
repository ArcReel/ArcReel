import { useTranslation } from "react-i18next";
import {
  ASSISTANT_PANEL_MAX_WIDTH,
  ASSISTANT_PANEL_MIN_WIDTH,
} from "@/stores/app-store";

interface Props {
  width: number;
  isResizing: boolean;
  onMouseDown: (e: React.MouseEvent<HTMLDivElement>) => void;
  onDoubleClick: () => void;
}

/**
 * 右侧 Agent 面板的左边缘 resize 手柄。
 * 4px 宽热区跨在边线上（-translate-x-1/2），内部 1px 高亮线在 hover / 拖动时显现。
 */
export function AssistantResizeHandle({
  width,
  isResizing,
  onMouseDown,
  onDoubleClick,
}: Props) {
  const { t } = useTranslation("dashboard");
  const label = t("resize_assistant_panel");

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- role="separator" 加 aria-value* 是 WAI-ARIA 的 window splitter 交互模式，jsx-a11y 未把 separator 列入交互角色
    <div
      role="separator"
      aria-orientation="vertical"
      aria-valuemin={ASSISTANT_PANEL_MIN_WIDTH}
      aria-valuemax={ASSISTANT_PANEL_MAX_WIDTH}
      aria-valuenow={width}
      aria-label={label}
      title={label}
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
      className="group absolute inset-y-0 left-0 z-10 w-1 -translate-x-1/2 cursor-col-resize select-none"
    >
      <div
        className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors duration-150 ${
          isResizing
            ? "bg-primary"
            : "bg-transparent group-hover:bg-primary"
        }`}
      />
    </div>
  );
}
