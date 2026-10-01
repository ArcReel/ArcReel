import { useId } from "react";
import { Bot } from "lucide-react";
import type { StepAct } from "./step-list";

interface Props {
  act: StepAct;
  onRun: (act: StepAct) => void;
  size?: "sm" | "md";
  /** 在「或者 …」、行内常驻入口与提醒里一律降为文字链，只有下一步的主入口保留按钮形态。 */
  asLink?: boolean;
  busy?: boolean;
}

/**
 * 下一步入口的统一形态，集页面板与顶栏弹层共用。主入口：「交给 Agent」实心、直接 AI 调用强调色描边、
 * 跳转与手动中性描边；降为文字链时（`asLink`）一律是下划线文字，破坏性操作始终是红字链。
 * 准入不满足时照常显示但不可点，悬停与读屏都给出原因。
 */
export function StepActButton({ act, onRun, size = "md", asLink = false, busy = false }: Props) {
  const reasonId = useId();
  const disabled = Boolean(act.disabledReason) || busy;
  const pad = size === "sm" ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-[12px]";
  const kind = asLink && act.kind !== "danger" ? "link" : act.kind;
  const common = {
    type: "button" as const,
    title: act.disabledReason ?? undefined,
    "aria-disabled": disabled || undefined,
    "aria-describedby": act.disabledReason ? reasonId : undefined,
    onClick: () => {
      if (!disabled) onRun(act);
    },
  };
  const reason = act.disabledReason ? (
    <span id={reasonId} className="sr-only">
      {act.disabledReason}
    </span>
  ) : null;
  const dim = disabled ? "cursor-not-allowed opacity-45" : "";

  if (kind === "agent") {
    return (
      <>
        <button
          {...common}
          className={`focus-ring inline-flex items-center gap-1 rounded-md font-medium ${pad} ${dim || "hover:opacity-90"}`}
          style={{
            background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            color: "oklch(0.15 0 0)",
          }}
        >
          <Bot aria-hidden className="h-3.5 w-3.5" />
          {act.label}
        </button>
        {reason}
      </>
    );
  }
  if (kind === "ai" || kind === "nav") {
    return (
      <>
        <button
          {...common}
          className={`focus-ring rounded-md font-medium ${pad} ${dim || "hover:opacity-80"}`}
          style={
            kind === "ai"
              ? { border: "1px solid var(--color-accent-soft)", color: "var(--color-accent-2)" }
              : { border: "1px solid var(--color-hairline)", color: "var(--color-text-2)" }
          }
        >
          {act.label}
        </button>
        {reason}
      </>
    );
  }
  return (
    <>
      <button
        {...common}
        className={`focus-ring rounded underline underline-offset-2 ${size === "sm" ? "text-[11.5px]" : "text-[12px]"} ${dim || "hover:opacity-80"}`}
        style={{ color: kind === "danger" ? "var(--color-danger-2)" : "var(--color-text-2)" }}
      >
        {act.label}
      </button>
      {reason}
    </>
  );
}
