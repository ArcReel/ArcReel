export interface InlineWarningAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** 灰化时的 hover 原因。 */
  title?: string;
}

export interface InlineWarningProps {
  /** 警告正文。 */
  message: string;
  /** 一键修正入口；只读上下文等不给入口的场景省略。 */
  action?: InlineWarningAction;
  /** 外层间距，由使用处的版式决定；排版与配色由本组件固定。 */
  className?: string;
}

/**
 * 行内警告：琥珀文案 + 可选的一键修正按钮。
 *
 * 承载「这项设置会让后续操作在执行期失败，但现在不阻断」这类提示——警告不门控控件，
 * 只说明后果并给出一条修正路径。`role="alert"` 让它在条件满足时立刻被朗读，因此挂在
 * 条件渲染的外层元素上：整块随条件出现，而非先渲染空壳再填内容。
 *
 * 修正按钮走本组件而非插槽：两处使用点的按钮外观与禁用行为必须一致，插槽会让它们各自漂移。
 */
export function InlineWarning({ message, action, className }: InlineWarningProps) {
  return (
    <div
      role="alert"
      className={`flex flex-wrap items-center gap-2 text-[12px] leading-[1.5] text-amber-300${
        className ? ` ${className}` : ""
      }`}
    >
      <span>{message}</span>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          disabled={action.disabled}
          title={action.title}
          className="rounded-[6px] border border-hairline-soft px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-text-2 transition-colors hover:border-hairline hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
