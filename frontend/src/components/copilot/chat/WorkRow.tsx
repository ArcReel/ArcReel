import { useMemo, useState, type ReactNode } from "react";
import { ChevronRight, CircleAlert, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { TERMINAL_SESSION_STATUSES } from "./utils";
import { toolLabel, type EpisodeRefs, type WorkLabel, type WorkStatus } from "./work-label";

// ---------------------------------------------------------------------------
// WorkRow: shared single-line tool/subagent/Skill/background-task work rows with icons, names, one-sentence summaries
// and states. Running uses breathing dots; failures turn icons/names red with failure marks; success has no marks. With details,
// entire rows trigger initially collapsed sections, sharing thinking-block line heights/indentation.
// ---------------------------------------------------------------------------

const NO_EPISODES: EpisodeRefs = [];

export function useSessionDone(): boolean {
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);
  return sessionStatus != null && TERMINAL_SESSION_STATUSES.has(sessionStatus);
}

/** Tool-call display names/summaries; episode titles use current ledgers and entry positions current project scripts. */
export function useToolLabel(name: string, input: Record<string, unknown> | undefined): WorkLabel {
  const { t } = useTranslation("dashboard");
  const episodes = useProjectsStore((s) => s.currentProjectData?.episodes) ?? NO_EPISODES;
  const scripts = useProjectsStore((s) => s.currentScripts);
  return useMemo(() => toolLabel(name, input, t, episodes, scripts), [name, input, t, episodes, scripts]);
}

interface WorkRowProps {
  icon: LucideIcon;
  name: string;
  summary?: string;
  status: WorkStatus;
  /** Expanded details; omission makes entire rows nonexpandable. */
  children?: ReactNode;
}

export function WorkRow({ icon: Icon, name, summary, status, children }: WorkRowProps) {
  const failed = status === "error";
  // Button owns icon sizing inside buttons; nonexpandable rows lack Buttons and set icon sizes themselves.
  const iconSize = children ? undefined : "size-3 shrink-0";
  const head = (
    <>
      <Icon data-icon="inline-start" aria-hidden className={cn(iconSize, failed && "text-destructive")} />
      <span className={cn("max-w-2/3 shrink-0 truncate", failed ? "text-destructive" : "text-subtle-foreground")}>
        {name}
      </span>
      {summary && <span className="min-w-0 truncate font-normal">{summary}</span>}
      <WorkStatusMark status={status} iconSize={iconSize} />
    </>
  );

  if (!children) {
    return (
      <div className="flex h-6 max-w-full min-w-0 items-center gap-1 text-xs font-medium text-muted-foreground">
        {head}
      </div>
    );
  }

  return (
    <Collapsible render={<div className="min-w-0 text-muted-foreground" />}>
      <CollapsibleTrigger render={<Button variant="ghost" size="xs" className="-ml-2 max-w-full" />}>
        {head}
        <ChevronRight data-icon="inline-end" aria-hidden className="transition-transform group-aria-expanded/button:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 mb-1.5 ml-1.5 flex max-w-[40em] min-w-0 flex-col gap-2 border-l border-border pl-3">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function WorkStatusMark({ status, iconSize }: { status: WorkStatus; iconSize?: string }) {
  const { t } = useTranslation("dashboard");
  switch (status) {
    case "running":
      return (
        <>
          <span aria-hidden className="size-1.5 shrink-0 animate-breath rounded-full bg-primary" />
          <span className="sr-only">{t("work_status_running")}</span>
        </>
      );
    case "error":
      return (
        <>
          <CircleAlert aria-hidden className={cn(iconSize, "text-destructive")} />
          <span className="sr-only">{t("work_status_failed")}</span>
        </>
      );
    case "stopped":
      return <span className="shrink-0 font-normal">{t("work_status_stopped")}</span>;
    default:
      return null;
  }
}

const MAX_COLLAPSED_LINES = 10;
const MAX_COLLAPSED_CHARS = 800;

/** Above 10 lines (or equivalent length), initially show beginnings without inner scrolling. */
function collapsedText(text: string): string | null {
  const lines = text.split("\n");
  let head = lines.length > MAX_COLLAPSED_LINES ? lines.slice(0, MAX_COLLAPSED_LINES).join("\n") : text;
  if (head.length > MAX_COLLAPSED_CHARS) head = head.slice(0, MAX_COLLAPSED_CHARS);
  return head === text ? null : `${head.trimEnd()}…`;
}

/** Expanded sections: small headings such as Arguments/Results with raw text. */
export function WorkDetail({ label, text, error = false }: { label: string; text: string; error?: boolean }) {
  const { t } = useTranslation("dashboard");
  const [showAll, setShowAll] = useState(false);
  const collapsed = useMemo(() => collapsedText(text), [text]);

  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <pre
        className={cn(
          "font-mono text-xs/relaxed break-all whitespace-pre-wrap",
          error ? "text-destructive" : "text-subtle-foreground",
        )}
      >
        {collapsed && !showAll ? collapsed : text}
      </pre>
      {collapsed && (
        <div>
          <Button variant="ghost" size="xs" className="-ml-2" onClick={() => setShowAll((value) => !value)}>
            {showAll ? t("work_show_less") : t("work_show_all")}
          </Button>
        </div>
      )}
    </div>
  );
}
