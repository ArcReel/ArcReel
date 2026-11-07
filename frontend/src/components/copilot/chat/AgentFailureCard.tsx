import { useEffect, useId, useState } from "react";
import { Check, ChevronRight, Copy, RotateCcw, Settings, TriangleAlert, Unplug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";

import { settingsSectionPath } from "@/app-routes";
import { Button, buttonVariants } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { FailureObservation } from "@/types";
import { copyText } from "@/utils/clipboard";

// ---------------------------------------------------------------------------
// AgentFailureCard: Agent failures with titles, one-sentence conclusions/actions, and raw information collapsed in Details.
//
// Localize backend stable conclusion keys (derived only from structured evidence; see
// server/agent_runtime/failure_observation.py), describing observations without inferring causes. The sole exception is
// subprocess_unsupported, whose exception class establishes causes and permits remedies in conclusions. Unknown
// keys/historical events predating this field use stage-generic keys. Offer only three cause-independent actions:
// Agent settings, Copy diagnostics, startup-failure Retry. Turn failures never offer retries that could replay executed tools.
// ---------------------------------------------------------------------------

const CONCLUSION_KEYS: Record<FailureObservation["phase"], ReadonlySet<string>> = {
  turn: new Set([
    "authentication_failed",
    "billing_error",
    "rate_limit",
    "invalid_request",
    "server_error",
    "error_max_turns",
    "error_max_budget_usd",
  ]),
  startup: new Set([
    "cli_not_found",
    "cli_connection_failed",
    "subprocess_unsupported",
    "process_failed",
    "startup_timeout",
  ]),
};
const GENERIC_KEY: Record<FailureObservation["phase"], string> = {
  turn: "turn_failed",
  startup: "startup_failed",
};

/** One-sentence conclusion keys: retain known values, otherwise use stage-generic fallbacks. */
function failureConclusionKey(failure: FailureObservation): string {
  const key = failure.summary.key;
  return key && CONCLUSION_KEYS[failure.phase].has(key) ? key : GENERIC_KEY[failure.phase];
}

const COPIED_RESET_MS = 2000;

interface AgentFailureCardProps {
  failure: FailureObservation;
  /** Failures newly arriving during viewing announce through alerts; failures already in loaded historical sessions remain silent. */
  announce?: boolean;
  /** Only current-page startup failures retaining original inputs provide this; never automatically replay historical turns. */
  onRetry?: () => void;
}

function display(value: unknown, fallback: string): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") return `${value}`;
  return JSON.stringify(value) ?? fallback;
}

export function AgentFailureCard({ failure, announce = false, onRetry }: Readonly<AgentFailureCardProps>) {
  const { t } = useTranslation("dashboard");
  const [copied, setCopied] = useState(false);
  const titleId = useId();
  const startup = failure.phase === "startup";
  const raw = JSON.stringify(failure, null, 2);
  const unavailable = t("agent_failure_not_provided");
  const facts: Array<[string, unknown]> = [
    [t("agent_failure_source_label"), failure.summary.source],
    [t("agent_failure_type_label"), failure.summary.type],
  ];
  if (failure.summary.status != null) {
    facts.push([t("agent_failure_status_label"), failure.summary.status]);
  }

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = () => {
    void copyText(raw).then(() => setCopied(true), () => setCopied(false));
  };

  const Icon = startup ? Unplug : TriangleAlert;

  return (
    <section
      role={announce ? "alert" : undefined}
      aria-labelledby={titleId}
      className="flex max-w-[40em] min-w-0 flex-col gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5"
    >
      <div className="flex items-start gap-2">
        <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="text-sm font-medium text-foreground">
            {t(startup ? "agent_failure_startup_title" : "agent_failure_turn_title")}
          </h3>
          <p className="mt-0.5 text-sm text-subtle-foreground">
            {t(`agent_failure_conclusion_${failureConclusionKey(failure)}`)}
          </p>
        </div>
      </div>

      <Collapsible render={<div className="flex min-w-0 flex-col gap-2" />}>
        <div className="flex flex-wrap items-center gap-1">
          {startup && onRetry && (
            <Button variant="outline" size="xs" onClick={onRetry}>
              <RotateCcw data-icon="inline-start" aria-hidden />
              {t("agent_failure_retry_startup")}
            </Button>
          )}
          <Link
            href={`~${settingsSectionPath("arcreel-agent")}`}
            className={buttonVariants({ variant: "ghost", size: "xs" })}
          >
            <Settings data-icon="inline-start" aria-hidden />
            {t("agent_failure_open_settings")}
          </Link>
          <Button variant="ghost" size="xs" onClick={copy}>
            {copied ? <Check data-icon="inline-start" aria-hidden /> : <Copy data-icon="inline-start" aria-hidden />}
            {t(copied ? "agent_failure_copied" : "agent_failure_copy")}
          </Button>
          <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="ml-auto" />}>
            {t("agent_failure_details_label")}
            <ChevronRight
              data-icon="inline-end"
              aria-hidden
              className="transition-transform group-aria-expanded/button:rotate-90"
            />
          </CollapsibleTrigger>
        </div>
        <CollapsibleContent>
          <div className="flex flex-col gap-2 border-t border-destructive/20 pt-2 text-xs text-subtle-foreground">
            <p className="text-muted-foreground">{t("agent_failure_observation_note")}</p>
            <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
              {facts.map(([label, value]) => (
                <div key={label} className="contents">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="min-w-0 font-mono break-all">{display(value, unavailable)}</dd>
                </div>
              ))}
              <div className="contents">
                <dt className="text-muted-foreground">{t("agent_failure_message_label")}</dt>
                <dd className="min-w-0 font-mono break-all whitespace-pre-wrap">
                  {display(failure.summary.message, unavailable)}
                </dd>
              </div>
            </dl>
            <p className="text-muted-foreground">{t("agent_failure_payload_label")}</p>
            <pre
              data-testid="failure-observation-json"
              className="rounded-md bg-background/60 p-2 font-mono break-all whitespace-pre-wrap"
            >
              {raw}
            </pre>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
