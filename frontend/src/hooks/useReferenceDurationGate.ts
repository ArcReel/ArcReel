import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ReferenceProjectionError, SpeechAdmissionError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { advisoryProblems } from "@/components/canvas/reference/advisory-problems";
import type { DurationConfirmItem } from "@/components/canvas/reference/ReferenceDurationConfirmDialog";

interface Options {
  projectName: string;
  episode: number;
}

/**
 * Whether this unit should still enqueue now, supplied by callers for current-entry semantics, reading live state rather than render snapshots:
 * preflight and user deliberation both await, allowing other entries/Agents to occupy the same unit.
 *
 * Entry-specific predicates, never one gate-wide rule: batch entries target units without finished videos, so exclude those completing
 * while dialogs remain open (completed tasks are no longer busy; queue deduplication sees queued/running only,
 * allowing them charges twice and overwrites fresh outputs). Unit Regenerate entries intentionally overwrite existing videos;
 * the same predicate would wrongly block those too.
 */
type CanEnqueue = (unitId: string) => boolean;

interface PendingConfirm {
  /** Units needing user decisions: tier deviations or nonblocking issues. */
  items: DurationConfirmItem[];
  /** All admitted units (including those needing no confirmation), enqueued in original order after confirmation. */
  unitIds: string[];
}

type ConfirmedDurations = ReadonlyMap<string, number>;
type Commit = (unitIds: string[], confirmedDurations: ConfirmedDurations) => Promise<void>;

/**
 * Presubmission confirmation gate for reference-video entries: preflight tiers, requiring confirmation if requested seconds differ from request-duration baselines
 * or preflight reports nonblocking issues (first N reference images). Cancellation enqueues nothing.
 *
 * Nonblocking issues trigger confirmation equally with tier deviations: they also alter output content, yet have no UI
 * display after persistence. Without presubmission notices users never hear them.
 *
 * Batch entries aggregate one confirmation (individual dialogs require N clicks for one operation). Single/batch entries share
 * the same gate, preventing batch buttons from bypassing confirmation.
 */
export function useReferenceDurationGate({ projectName, episode }: Options) {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  // Capture enqueue callbacks with run: confirmation can happen anytime afterward, never reread render closures.
  const commitRef = useRef<Commit | null>(null);
  // Capture recheck predicates with run too: entry semantics differ; confirmation needs the entry initiating this round.
  const canEnqueueRef = useRef<CanEnqueue | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Project/episode switches invalidate in-flight preflights and pending dialogs, both bound to prior data. Confirmation
  // after switches would enqueue the previous episode's units.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
      setPending(null);
      commitRef.current = null;
      canEnqueueRef.current = null;
    };
  }, [projectName, episode]);

  const run = useCallback(
    async (
      unitIds: string[],
      commit: Commit,
      canEnqueue: CanEnqueue,
    ) => {
      if (unitIds.length === 0) return;
      // Taking-over requests rotate controllers: new preflight invalidates predecessors and replaces pending confirmations.
      // Clear dialogs and commit together now, never wait for next results: if every new unit needs no
      // confirmation (direct commit/return), old dialogs otherwise remain and confirming enqueues previous inventories.
      abortRef.current?.abort();
      setPending(null);
      commitRef.current = null;
      canEnqueueRef.current = null;
      const controller = new AbortController();
      abortRef.current = controller;
      const { signal } = controller;

      const results = await Promise.all(
        unitIds.map(async (unitId) => {
          try {
            const precheck = await API.precheckReferenceVideoDuration(
              projectName,
              episode,
              unitId,
              { signal },
            );
            return { unitId, precheck };
          } catch (e) {
            if (signal.aborted) return null;
            return { unitId, error: e };
          }
        }),
      );
      if (signal.aborted) return;

      const ok: DurationConfirmItem[] = [];
      let failed = 0;
      const admissionMessages = new Set<string>();
      for (const result of results) {
        if (!result) continue;
        if ("error" in result) {
          if (result.error instanceof SpeechAdmissionError || result.error instanceof ReferenceProjectionError) {
            admissionMessages.add(result.error.message);
          } else {
            failed += 1;
          }
          continue;
        }
        ok.push(result);
      }
      // Never enqueue failed preflight units: duration-baseline agreement is unknown, and silent tier generation wastes quotas.
      if (admissionMessages.size > 0) {
        useAppStore.getState().pushToast(Array.from(admissionMessages).join("\n"), "error");
      }
      if (failed > 0) {
        useAppStore
          .getState()
          .pushToast(t("reference_duration_precheck_failed", { count: failed }), "error");
      }
      if (ok.length === 0) return;

      // Recheck when opening dialogs: during preflight awaits, units may have enqueued elsewhere or completed;
      // listing them asks users to decide operations that cannot or should not run.
      // Silently skip instead of errors, matching batch entries enqueueing everything still generatable.
      const available = ok.filter((item) => canEnqueue(item.unitId));
      if (available.length === 0) return;

      const needsConfirmation = available.filter(
        (item) => item.precheck.needs_confirmation || advisoryProblems(item.precheck).length > 0,
      );
      const passing = available.map((item) => item.unitId);
      if (needsConfirmation.length === 0) {
        await commit(passing, new Map());
        return;
      }
      commitRef.current = commit;
      canEnqueueRef.current = canEnqueue;
      setPending({ items: needsConfirmation, unitIds: passing });
    },
    [projectName, episode, t],
  );

  const confirm = useCallback(() => {
    const current = pending;
    const commit = commitRef.current;
    const canEnqueue = canEnqueueRef.current;
    setPending(null);
    commitRef.current = null;
    canEnqueueRef.current = null;
    if (!current || !commit) return;
    // Recheck again on submission: during potentially lengthy user deliberation, listed units may have been
    // enqueued elsewhere or, in batches, completed; frozen-inventory submission would double bill.
    const targets = canEnqueue ? current.unitIds.filter(canEnqueue) : current.unitIds;
    if (targets.length === 0) return;
    // commit already reports failures by entry semantics; only catch unexpected escaping exceptions here.
    // Declare confirmed tiers only for tier-deviation units: units listed solely for nonblocking issues have no tiers needing decisions;
    // declaring one would let servers skip their own tier validation.
    const confirmedDurations = new Map(
      current.items
        .filter((item) => item.precheck.needs_confirmation)
        .map((item) => [item.unitId, item.precheck.request_duration]),
    );
    void commit(targets, confirmedDurations).catch((e: unknown) => {
      useAppStore
        .getState()
        .pushToast(t("reference_generate_request_failed", { error: errMsg(e) }), "error");
    });
  }, [pending, t]);

  const cancel = useCallback(() => {
    setPending(null);
    commitRef.current = null;
    canEnqueueRef.current = null;
  }, []);

  return {
    run,
    /** Spread directly into ReferenceDurationConfirmDialog. */
    dialogProps: {
      open: pending !== null,
      items: pending?.items ?? [],
      onConfirm: confirm,
      onCancel: cancel,
    },
  };
}
