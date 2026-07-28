import { useId, useState } from "react";
import { Check, ChevronRight, Copy, RotateCcw, Settings, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type { FailureObservation } from "@/types";
import { copyText } from "@/utils/clipboard";

interface AgentFailureCardProps {
  failure: FailureObservation;
  /** 只由当前页面内、仍保留原始输入的启动失败提供；历史轮次绝不自动重放。 */
  onRetry?: () => void;
}

function display(value: unknown, fallback: string): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") return `${value}`;
  return JSON.stringify(value) ?? fallback;
}

export function AgentFailureCard({ failure, onRetry }: Readonly<AgentFailureCardProps>) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const raw = JSON.stringify(failure, null, 2);
  const unavailable = t("agent_failure_not_provided");
  const startup = failure.phase === "startup";
  const facts: Array<[string, unknown]> = [
    [t("agent_failure_source_label"), failure.summary.source],
    [t("agent_failure_type_label"), failure.summary.type],
  ];
  if (failure.summary.status != null) {
    facts.push([t("agent_failure_status_label"), failure.summary.status]);
  }

  const copy = () => {
    void copyText(raw).then(() => setCopied(true), () => setCopied(false));
  };

  return (
    <section
      role="alert"
      aria-labelledby={titleId}
      className="min-w-0 overflow-hidden rounded-xl border border-warm-bright/30 bg-warm-bright/[0.04]"
    >
      <div className="space-y-3 p-3.5">
        <header className="flex items-start gap-2.5">
          <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warm-bright" />
          <div className="min-w-0">
            <h3 id={titleId} className="text-[13px] font-semibold text-text">
              {t(startup ? "agent_failure_startup_title" : "agent_failure_turn_title")}
            </h3>
            <p className="mt-1 text-[11px] leading-relaxed text-text-3">
              {t("agent_failure_observation_note")}
            </p>
          </div>
        </header>

        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[11px]">
          {facts.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-text-4">{label}</dt>
              <dd className="min-w-0 break-all font-mono text-text-2">{display(value, unavailable)}</dd>
            </div>
          ))}
        </dl>

        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-hairline-soft bg-bg-grad-a/60 p-2.5 font-mono text-[11px] leading-relaxed text-text-2">
          {display(failure.summary.message, unavailable)}
        </pre>
      </div>

      <details className="group border-t border-hairline-soft px-3.5 py-2 text-[11px] text-text-3">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          <ChevronRight aria-hidden className="h-3 w-3 transition-transform group-open:rotate-90" />
          {t("agent_failure_details_label")}
        </summary>
        <pre
          data-testid="failure-observation-json"
          className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-hairline-soft bg-bg-grad-a/60 p-2.5 font-mono text-[10.5px] leading-relaxed"
        >
          {raw}
        </pre>
      </details>

      <footer className="flex flex-wrap gap-2 border-t border-hairline-soft px-3.5 py-2.5">
        <button type="button" onClick={copy} className={GHOST_BTN_CLS}>
          {copied ? <Check aria-hidden className="h-3.5 w-3.5" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
          {t(copied ? "agent_failure_copied" : "agent_failure_copy")}
        </button>
        <Link href="/app/settings?section=agent" className={GHOST_BTN_CLS}>
          <Settings aria-hidden className="h-3.5 w-3.5" />
          {t("agent_failure_open_settings")}
        </Link>
        {startup && onRetry && (
          <button type="button" onClick={onRetry} className={GHOST_BTN_CLS}>
            <RotateCcw aria-hidden className="h-3.5 w-3.5" />
            {t("agent_failure_retry_startup")}
          </button>
        )}
      </footer>
    </section>
  );
}
