import { useTranslation } from "react-i18next";

// ---------------------------------------------------------------------------
// SkillChip – inline `/skill-name` chip for skill invocations.
//
// 工序行视觉语言：机器工序收敛为低对比单行。skill 调用只显示名与入参
// （写入点定型，注入全文不在日志中），不可展开。
// ---------------------------------------------------------------------------

export type SkillChipStatus = "running" | "ok" | "error";

interface SkillChipProps {
  name?: string;
  args?: string;
  status?: SkillChipStatus;
}

export function SkillChip({ name, args, status }: SkillChipProps) {
  const { t } = useTranslation("dashboard");
  const displayName = name || t("skill_chip_unknown");

  const statusIcon = status === "ok" ? "✓" : status === "error" ? "✗" : status === "running" ? "…" : null;
  const statusColor =
    status === "ok" ? "var(--color-good)" : status === "error" ? "var(--color-danger)" : "var(--color-text-4)";

  return (
    <div className="my-1 flex min-w-0 items-center gap-1.5">
      <span
        className="num inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium"
        style={{ background: "var(--color-accent-dim)", color: "var(--color-accent-2)" }}
      >
        /{displayName}
      </span>
      {args && (
        <span className="truncate text-[11px]" style={{ color: "var(--color-text-3)" }} title={args}>
          {args}
        </span>
      )}
      {statusIcon && (
        <span className="shrink-0 text-[11px] font-medium" style={{ color: statusColor }}>
          {statusIcon}
        </span>
      )}
    </div>
  );
}
