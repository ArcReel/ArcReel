import { useId, useMemo, useState } from "react";
import { Check, Loader2, SearchIcon, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { useAssistantStore } from "@/stores/assistant-store";
import type { SessionMeta } from "@/types";
import { formatRelativeTime, isJustNow } from "@/utils/date-format";
import { sessionTitle } from "./session-title";

function SessionStatusDot({ status }: { status: SessionMeta["status"] }) {
  const { t } = useTranslation("dashboard");
  const label =
    status === "running"
      ? t("session_status_running")
      : status === "error"
        ? t("session_status_error")
        : status === "interrupted"
          ? t("session_status_interrupted")
          : status === "completed"
            ? t("session_status_completed")
            : t("session_status_idle");
  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        status === "running" && "animate-breath bg-primary",
        status === "error" && "bg-destructive",
        status !== "running" && status !== "error" && "bg-muted-foreground/40",
      )}
    />
  );
}

interface SessionHistoryProps {
  onPick: (sessionId: string) => void;
  /** Delete sessions, returning success. */
  onDelete: (sessionId: string) => Promise<boolean>;
  /** Close history views and return to messages. */
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// SessionHistory replaces message regions with session lists when History opens.
// Search titles; rows show status dots/relative times and end Delete actions, irreversible after confirmation.
// ---------------------------------------------------------------------------

export function SessionHistory({ onPick, onDelete, onClose }: SessionHistoryProps) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const sessions = useAssistantStore((s) => s.sessions);
  const currentSessionId = useAssistantStore((s) => s.currentSessionId);
  const [query, setQuery] = useState("");
  const [toDelete, setToDelete] = useState<SessionMeta | null>(null);
  const headingId = useId();

  const matched = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((session) => sessionTitle(session, t).toLowerCase().includes(q));
  }, [query, sessions, t]);

  const relativeTime = (iso: string) =>
    isJustNow(iso) ? t("dashboard:session_updated_just_now") : (formatRelativeTime(iso, i18n.language) ?? "");

  return (
    // Handle only Esc bubbling from search inputs/list rows; regions themselves are noninteractive.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- See previous line
    <section
      aria-labelledby={headingId}
      className="flex min-h-0 flex-1 flex-col"
      onKeyDown={(event) => {
        // Esc first returns to messages; prevent defaults so shells do not collapse panels.
        // Delete-confirmation Portals also bubble Esc through React trees; handle only keys inside this view DOM.
        if (
          event.key === "Escape" &&
          !event.defaultPrevented &&
          event.currentTarget.contains(event.target as Node)
        ) {
          event.preventDefault();
          onClose();
        }
      }}
    >
      <div className="flex shrink-0 flex-col gap-2 px-3 pt-3 pb-2">
        <h2 id={headingId} className="text-xs font-medium text-muted-foreground">
          {t("dashboard:session_history_title")}
        </h2>
        <InputGroup>
          <InputGroupInput
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("dashboard:session_search_placeholder")}
            aria-label={t("dashboard:session_search_placeholder")}
          />
          <InputGroupAddon>
            <SearchIcon aria-hidden />
          </InputGroupAddon>
        </InputGroup>
      </div>

      <div className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-3 [scrollbar-gutter:stable]">
        {matched.length === 0 ? (
          <p role="status" className="px-2 py-8 text-center text-sm text-muted-foreground">
            {sessions.length === 0 ? t("dashboard:session_history_empty") : t("dashboard:session_search_no_match")}
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {matched.map((session) => {
              const title = sessionTitle(session, t);
              const current = session.id === currentSessionId;
              return (
                <li key={session.id} className="group/session flex min-w-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="min-w-0 flex-1 justify-start"
                    aria-current={current ? "true" : undefined}
                    onClick={() => onPick(session.id)}
                  >
                    <SessionStatusDot status={session.status} />
                    <TruncatedText
                      text={title}
                      focusable={false}
                      className={cn("flex-1 text-left", current ? "text-foreground" : "font-normal text-subtle-foreground")}
                    />
                    {current && <Check aria-hidden className="text-muted-foreground" />}
                    <span className="shrink-0 text-xs font-normal text-muted-foreground">{relativeTime(session.updated_at)}</span>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t("dashboard:delete_session_named", { title })}
                    onClick={() => setToDelete(session)}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <DeleteSessionDialog session={toDelete} onDelete={onDelete} onClose={() => setToDelete(null)} />
    </section>
  );
}

function DeleteSessionDialog({
  session,
  onDelete,
  onClose,
}: {
  session: SessionMeta | null;
  onDelete: (sessionId: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  // Retain previous sessions during closing animations so titles never flash empty.
  const [shown, setShown] = useState(session);
  if (session !== null && session !== shown) setShown(session);
  const [deleting, setDeleting] = useState(false);
  const [failed, setFailed] = useState(false);

  const close = () => {
    setFailed(false);
    onClose();
  };

  const confirm = async () => {
    if (!session || deleting) return;
    setDeleting(true);
    setFailed(false);
    let deleted: boolean;
    try {
      deleted = await onDelete(session.id);
    } finally {
      setDeleting(false);
    }
    // Failed deletion retains sessions; explain within dialogs for retries.
    if (!deleted) {
      setFailed(true);
      return;
    }
    close();
  };

  const title = sessionTitle(shown ?? undefined, t);

  return (
    <AlertDialog
      open={session !== null}
      onOpenChange={(open) => {
        // Ignore Esc/backdrop clicks while deleting; dialogs persist until settlement.
        if (!open && !deleting) close();
      }}
    >
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:delete_session_title", { title })}</AlertDialogTitle>
          <AlertDialogDescription>{t("dashboard:delete_session_desc")}</AlertDialogDescription>
        </AlertDialogHeader>
        {failed && (
          // Put failure explanations in sole scroll regions so short windows never clip them between headers/footers.
          <AlertDialogBody tabIndex={0} role="region" aria-label={t("dashboard:delete_session_title", { title })}>
            <p role="alert" className="text-destructive">
              {t("dashboard:delete_session_failed")}
            </p>
          </AlertDialogBody>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void confirm()}>
            {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("dashboard:delete_session")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
