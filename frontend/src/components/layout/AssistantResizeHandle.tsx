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
 * 右侧助手面板的左边缘 resize 手柄。
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
    // role="separator" + aria-valuenow/min/max 是 WAI-ARIA Authoring Practices
    // 中的 window splitter 模式（交互式控件），但 jsx-a11y 默认未把 separator
    // 列入 interactive 角色，故就地抑制 noninteractive-element 规则。
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
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
            ? "bg-[var(--color-accent)]"
            : "bg-transparent group-hover:bg-[var(--color-accent)]"
        }`}
      />
    </div>
  );
}
