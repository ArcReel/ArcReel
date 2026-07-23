import { useId, useState } from "react";
import { Check, ChevronRight, Copy, RotateCcw, Settings, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import type { FailureObservation } from "@/types";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { copyText } from "@/utils/clipboard";

interface AgentFailureCardProps {
  failure: FailureObservation;
  /** 只由当前页面内、仍保留原始输入的启动失败提供；历史轮次绝不自动重放。 */
  onRetry?: () => void;
}

function observedValue(value: unknown, fallback: string): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  try {
    return JSON.stringify(value) ?? fallback;
  } catch {
    return fallback;
  }
}

export function AgentFailureCard({ failure, onRetry }: Readonly<AgentFailureCardProps>) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const serialized = JSON.stringify(failure, null, 2);
  const unavailable = t("agent_failure_not_provided");
  const isStartup = failure.phase === "startup";

  const handleCopy = () => {
    void copyText(serialized)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };

  return (
    <section
      role="alert"
      aria-labelledby={titleId}
      className="relative min-w-0 overflow-hidden rounded-xl border"
      style={{
        borderColor: "oklch(0.70 0.18 25 / 0.34)",
        background:
          "linear-gradient(135deg, oklch(0.23 0.025 25 / 0.78), oklch(0.18 0.012 265 / 0.82) 52%)",
        boxShadow: "inset 3px 0 0 oklch(0.70 0.18 25 / 0.78)",
      }}
    >
      <div className="px-3.5 pb-3 pt-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span
            className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg"
            style={{
              color: "oklch(0.82 0.13 25)",
              background: "oklch(0.70 0.18 25 / 0.14)",
              border: "1px solid oklch(0.70 0.18 25 / 0.22)",
            }}
          >
            <TriangleAlert aria-hidden className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 id={titleId} className="text-[13px] font-semibold" style={{ color: "var(--color-text)" }}>
                {t(isStartup ? "agent_failure_startup_title" : "agent_failure_turn_title")}
              </h3>
              <span
                className="rounded border px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.08em]"
                style={{
                  borderColor: "oklch(0.70 0.18 25 / 0.26)",
                  color: "oklch(0.82 0.10 25)",
                  background: "oklch(0.70 0.18 25 / 0.08)",
                }}
              >
                {t(isStartup ? "agent_failure_phase_startup" : "agent_failure_phase_turn")} · {failure.phase}
              </span>
            </div>
            <p className="mt-1 text-[11px] leading-[1.45]" style={{ color: "var(--color-text-3)" }}>
              {t("agent_failure_observation_note")}
            </p>
          </div>
        </div>

        <dl className="mt-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[11px]">
          <dt style={{ color: "var(--color-text-4)" }}>{t("agent_failure_source_label")}</dt>
          <dd className="min-w-0 break-all font-mono" style={{ color: "var(--color-text-2)" }}>
            {observedValue(failure.summary.source, unavailable)}
          </dd>
          <dt style={{ color: "var(--color-text-4)" }}>{t("agent_failure_type_label")}</dt>
          <dd className="min-w-0 break-all font-mono" style={{ color: "var(--color-text-2)" }}>
            {observedValue(failure.summary.type, unavailable)}
          </dd>
          {failure.summary.status !== null && failure.summary.status !== undefined && (
            <>
              <dt style={{ color: "var(--color-text-4)" }}>{t("agent_failure_status_label")}</dt>
              <dd className="min-w-0 break-all font-mono" style={{ color: "var(--color-text-2)" }}>
                {observedValue(failure.summary.status, unavailable)}
              </dd>
            </>
          )}
        </dl>

        <div className="mt-3">
          <div
            className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("agent_failure_message_label")}
          </div>
          <pre
            className="max-h-40 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-lg border px-2.5 py-2 font-mono text-[11px] leading-[1.5]"
            style={{
              borderColor: "var(--color-hairline-soft)",
              background: "oklch(0.13 0.008 265 / 0.64)",
              color: "var(--color-text-2)",
            }}
          >
            {observedValue(failure.summary.message, unavailable)}
          </pre>
        </div>
      </div>

      <details className="group border-t" style={{ borderColor: "var(--color-hairline-soft)" }}>
        <summary
          className="flex cursor-pointer list-none items-center gap-1.5 px-3.5 py-2 text-[11px] transition-colors hover:bg-white/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
          style={{ color: "var(--color-text-3)" }}
        >
          <ChevronRight aria-hidden className="h-3 w-3 transition-transform group-open:rotate-90" />
          {t("agent_failure_details_label")}
        </summary>
        <div className="px-3.5 pb-3">
          <pre
            data-testid="failure-observation-json"
            className="max-h-72 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-lg border px-2.5 py-2 font-mono text-[10.5px] leading-[1.5]"
            style={{
              borderColor: "var(--color-hairline-soft)",
              background: "oklch(0.12 0.008 265 / 0.78)",
              color: "var(--color-text-3)",
            }}
          >
            {serialized}
          </pre>
        </div>
      </details>

      <div
        className="flex flex-wrap items-center gap-2 border-t px-3.5 py-2.5"
        style={{
          borderColor: "var(--color-hairline-soft)",
          background: "oklch(0.13 0.008 265 / 0.26)",
        }}
      >
        <button type="button" onClick={handleCopy} className={GHOST_BTN_CLS}>
          {copied
            ? <Check aria-hidden className="h-3.5 w-3.5" />
            : <Copy aria-hidden className="h-3.5 w-3.5" />}
          {t(copied ? "agent_failure_copied" : "agent_failure_copy")}
        </button>
        <Link href="/app/settings?section=agent" className={GHOST_BTN_CLS}>
          <Settings aria-hidden className="h-3.5 w-3.5" />
          {t("agent_failure_open_settings")}
        </Link>
        {isStartup && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className={`${GHOST_BTN_CLS} border-[oklch(0.70_0.18_25_/_0.32)] text-[oklch(0.84_0.10_25)]`}
          >
            <RotateCcw aria-hidden className="h-3.5 w-3.5" />
            {t("agent_failure_retry_startup")}
          </button>
        )}
      </div>
    </section>
  );
}
