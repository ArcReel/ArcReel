import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ReferenceProjectionError, SpeechAdmissionError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { DurationConfirmItem } from "@/components/canvas/reference/ReferenceDurationConfirmDialog";

interface Options {
  projectName: string;
  episode: number;
  onEditReferences?: (unitId: string) => void;
}

type CanEnqueue = (unitId: string) => boolean;

interface PendingConfirm {
  items: DurationConfirmItem[];
  unitIds: string[];
}

type ConfirmedDurations = ReadonlyMap<string, number>;
type Commit = (unitIds: string[], confirmedDurations: ConfirmedDurations) => Promise<void>;

export function useReferenceDurationGate({ projectName, episode, onEditReferences }: Options) {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const commitRef = useRef<Commit | null>(null);
  const canEnqueueRef = useRef<CanEnqueue | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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
          if (onEditReferences && result.error instanceof ReferenceProjectionError &&
              result.error.projection.problems.some((problem) => problem.code === "video_reference_images_exceeded")) {
            useAppStore.getState().pushToast(result.error.message, "error", {
              action: {
                label: t("reference_edit_unit_references", { id: result.unitId }),
                onClick: () => onEditReferences(result.unitId),
              },
            });
          } else if (result.error instanceof SpeechAdmissionError || result.error instanceof ReferenceProjectionError) {
            admissionMessages.add(result.error.message);
          } else {
            failed += 1;
          }
          continue;
        }
        ok.push(result);
      }
      if (admissionMessages.size > 0) {
        useAppStore.getState().pushToast(Array.from(admissionMessages).join("\n"), "error");
      }
      if (failed > 0) {
        useAppStore
          .getState()
          .pushToast(t("reference_duration_precheck_failed", { count: failed }), "error");
      }
      if (ok.length === 0) return;

      const available = ok.filter((item) => canEnqueue(item.unitId));
      if (available.length === 0) return;

      const needsConfirmation = available.filter(
        (item) => item.precheck.needs_confirmation,
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
    [projectName, episode, t, onEditReferences],
  );

  const confirm = useCallback(() => {
    const current = pending;
    const commit = commitRef.current;
    const canEnqueue = canEnqueueRef.current;
    setPending(null);
    commitRef.current = null;
    canEnqueueRef.current = null;
    if (!current || !commit) return;
    const targets = canEnqueue ? current.unitIds.filter(canEnqueue) : current.unitIds;
    if (targets.length === 0) return;
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
    dialogProps: {
      open: pending !== null,
      items: pending?.items ?? [],
      onConfirm: confirm,
      onCancel: cancel,
    },
  };
}
