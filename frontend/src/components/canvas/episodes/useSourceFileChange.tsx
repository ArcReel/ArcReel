import { useCallback, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { SourceFileImpact } from "@/types/episodes-view";

import { ImpactConfirmDialog } from "./ImpactConfirmDialog";

/** Whole-source file-change response: uploads carry fields besides `status`; retain only confirmation-protocol fields here. */
export interface SourceFileChangeReply {
  status?: "applied" | "confirmation_required";
  impact?: SourceFileImpact & { text?: string };
  revision?: string;
}

interface PendingConfirmation {
  title: string;
  confirmLabel: string;
  text: string;
  danger: boolean;
  /** The server's list changed during confirmation; this is the updated list. */
  changed: boolean;
}

/** Confirm as dangerous when listed episodes would become stale, retired, or removed. */
function isDanger(impact: SourceFileImpact | undefined): boolean {
  if (!impact) return false;
  return impact.changed_with_products.length + impact.retired.length + impact.removed.length > 0;
}

/**
 * Shared confirmation for whole-source file insertion, replacement, editing, deletion, and reordering.
 *
 * `run(title, confirmLabel, call)` first submits `call(null)`; without affected episodes the server executes immediately. If confirmation is needed, show only
 * the server-written affected-episode list. Resubmit with its `revision` after confirmation; if it changed meanwhile, show the new list for confirmation again.
 * Return the executed response, or null if the creator cancels. Request errors close confirmation and propagate to callers.
 *
 * `busy` is true during each submission, false while awaiting creator confirmation. Calling before the previous `run` ends returns null without requests.
 */
export function useSourceFileChange() {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<PendingConfirmation | null>(null);
  const [busy, setBusy] = useState(false);
  const answer = useRef<((confirmed: boolean) => void) | null>(null);
  const running = useRef(false);

  const ask = useCallback(
    (next: PendingConfirmation) =>
      new Promise<boolean>((resolve) => {
        answer.current = resolve;
        setPending(next);
      }),
    [],
  );

  const run = useCallback(
    async <R extends SourceFileChangeReply>(
      title: string,
      confirmLabel: string,
      call: (revision: string | null) => Promise<R>,
    ): Promise<R | null> => {
      if (running.current) return null;
      running.current = true;
      const submit = async (revision: string | null) => {
        setBusy(true);
        try {
          return await call(revision);
        } finally {
          setBusy(false);
        }
      };
      try {
        let reply = await submit(null);
        let changed = false;
        while (reply.status === "confirmation_required") {
          const confirmed = await ask({
            title,
            confirmLabel,
            text: reply.impact?.text ?? "",
            danger: isDanger(reply.impact),
            changed,
          });
          if (!confirmed) return null;
          reply = await submit(reply.revision ?? null);
          changed = true;
        }
        return reply;
      } finally {
        running.current = false;
        answer.current = null;
        setPending(null);
      }
    },
    [ask],
  );

  const respond = (confirmed: boolean) => answer.current?.(confirmed);

  const dialog: ReactNode = (
    <ImpactConfirmDialog
      request={
        pending === null
          ? null
          : {
              title: pending.title,
              body: (
                <div className="flex flex-col gap-2">
                  {pending.changed ? <p className="text-warn">{t("source_file_change_changed")}</p> : null}
                  <p>{t("source_file_change_impact")}</p>
                  <p className="whitespace-pre-line">{pending.text}</p>
                </div>
              ),
              confirmLabel: pending.confirmLabel,
              runningLabel: t("source_file_change_running"),
              destructive: pending.danger,
            }
      }
      busy={busy}
      onConfirm={() => respond(true)}
      onCancel={() => respond(false)}
    />
  );

  return { run, busy, dialog };
}
