import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import { draftRepairResourceId, enqueueDraftRepair } from "@/actions/generation";
import type { DraftRepairTaskResult, SaveEpisodeDraftResult, ScriptReviewQuarantine } from "@/types";
import { useAppStore } from "@/stores/app-store";
import {
  isOccupyingStatus,
  isResourceBusy,
  useActiveResourceIds,
  useLatestTasksByResource,
  useTaskRowsByIds,
} from "@/stores/tasks-store";

interface DraftEditorOptions<T> {
  projectName: string;
  episode: number;
  /** Server draft view (draft needing repairs); null means this hook owns no edits. */
  view: ScriptReviewQuarantine | null;
  /**
   * Narrow draft bodies to panel-editable shapes; null on corrupt structure makes panels show readonly explanations.
   * Requires stable references (module functions or `useCallback`).
   */
  narrow: (content: Record<string, unknown> | null) => T | null;
  /** Notify callers after save/discard/AI repair settles to refresh server state (adoption changes formal content). */
  onSettled: () => void;
}

interface Synced<T> {
  revision: string | null;
  base: T | null;
  edited: T | null;
}

export interface DraftEditorHandle<T> {
  /** Currently edited draft body; null if structures cannot narrow to editable shapes. */
  content: T | null;
  setContent: (update: (prev: T) => T) => void;
  dirty: boolean;
  /** Server drafts changed elsewhere while local edits remain unsaved: saving fails; discard edits to load latest. */
  outdated: boolean;
  saving: boolean;
  discarding: boolean;
  /** AI repair for this draft is submitting, queued or executing. */
  repairing: boolean;
  /** Save/validate; adopt when all violations clear. */
  save: () => Promise<void>;
  /** Submit AI repair of saved drafts; `true` means submitted or settled, allowing callers to close dialogs. */
  repair: (instructions: string) => Promise<boolean>;
  /** Discard drafts; return success so callers decide whether to close confirmations. */
  discard: () => Promise<boolean>;
  /** Discard local edits and load latest server drafts. */
  reloadLatest: () => void;
}

function clone<T>(value: T | null): T | null {
  return value == null ? null : structuredClone(value);
}

function isDirty<T>(synced: Synced<T>): boolean {
  return JSON.stringify(synced.edited) !== JSON.stringify(synced.base);
}

export function diagnosticCode(err: unknown): string | null {
  if (!(err instanceof ApiRequestError)) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const code = (diagnostic as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

/**
 * In-place manual repair of drafts: local edits, save/validate (adopt with zero violations), AI repair and discard.
 *
 * Server drafts changing through Agent writes/other saves are adopted directly without unsaved local edits; otherwise retain edits,
 * mark `outdated` and reject saves as concurrency conflicts, then load latest drafts.
 *
 * AI repairs execute as queued text tasks; `repairing` remains true while owning this draft's task slot. When repairs submitted here or observed
 * running become terminal, notify results and refresh callers. Adoption removes draft views, so track task IDs.
 */
export function useDraftEditor<T>({ projectName, episode, view, narrow, onSettled }: DraftEditorOptions<T>): DraftEditorHandle<T> {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const [synced, setSynced] = useState<Synced<T> | null>(null);
  const [saving, setSaving] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [watchedRepairId, setWatchedRepairId] = useState<string | null>(null);

  const repairResourceId = view != null ? draftRepairResourceId(episode, view.doc_type) : null;
  const activeRepairs = useActiveResourceIds("text_draft_repair", projectName);
  const repairing = repairResourceId != null && activeRepairs.has(repairResourceId);
  const latestRepair = useLatestTasksByResource(projectName, "text_draft_repair").get(repairResourceId ?? "");
  // Also track this draft's observed running repairs submitted elsewhere or found after reopening pages.
  if (latestRepair != null && isOccupyingStatus(latestRepair.status) && watchedRepairId !== latestRepair.task_id) {
    setWatchedRepairId(latestRepair.task_id);
  }
  const watchedRepair = useTaskRowsByIds(watchedRepairId != null ? [watchedRepairId] : []).get(watchedRepairId ?? "");

  const incomingRevision = view?.revision ?? null;
  const adoptView = useCallback(
    (next: ScriptReviewQuarantine) => {
      const base = narrow(next.content);
      setSynced({ revision: next.revision, base, edited: clone(base) });
    },
    [narrow],
  );

  // Derived state for server-draft changes (synchronous during render): adopt only without unsaved edits.
  if (view != null && (synced == null || (synced.revision !== incomingRevision && !isDirty(synced)))) {
    adoptView(view);
  }
  if (view == null && synced != null) {
    setSynced(null);
  }

  const current = view != null ? synced : null;
  const dirty = current != null && isDirty(current);
  const outdated = current != null && current.revision !== incomingRevision;

  const setContent = useCallback((update: (prev: T) => T) => {
    setSynced((prev) => (prev?.edited == null ? prev : { ...prev, edited: update(prev.edited) }));
  }, []);

  const reloadLatest = useCallback(() => {
    if (view != null) adoptView(view);
  }, [view, adoptView]);

  const save = useCallback(async () => {
    if (view == null || current?.edited == null) return;
    setSaving(true);
    try {
      const result: SaveEpisodeDraftResult = await API.saveEpisodeDraft(
        projectName,
        episode,
        view.doc_type,
        current.edited,
        current.revision ?? "",
      );
      if (result.adopted) {
        pushToast(t("draft_adopted_toast"), "success");
      } else if (result.draft != null) {
        adoptView(result.draft);
        pushToast(t("draft_saved_with_violations_toast", { count: result.draft.violations.length }), "warning");
      }
      onSettled();
    } catch (err) {
      if (diagnosticCode(err) === "revision_conflict") {
        adoptView(view);
        pushToast(t("draft_conflict_toast"), "warning");
        onSettled();
      } else {
        pushToast(err instanceof Error && err.message ? err.message : t("draft_save_failed_toast"), "error");
      }
    } finally {
      setSaving(false);
    }
  }, [view, current, projectName, episode, adoptView, onSettled, pushToast, t]);

  const repair = useCallback(
    async (instructions: string): Promise<boolean> => {
      if (view == null) return false;
      if (isResourceBusy("text_draft_repair", projectName, draftRepairResourceId(episode, view.doc_type))) {
        pushToast(t("draft_repair_busy"), "error");
        return false;
      }
      try {
        const { taskIds } = await enqueueDraftRepair(
          projectName,
          episode,
          view.doc_type,
          current?.revision ?? view.revision ?? "",
          instructions.trim() || null,
        );
        if (taskIds[0] != null) setWatchedRepairId(taskIds[0]);
        return true;
      } catch (err) {
        if (diagnosticCode(err) === "revision_conflict") {
          adoptView(view);
          pushToast(t("draft_conflict_toast"), "warning");
          onSettled();
          return true;
        }
        pushToast(err instanceof Error && err.message ? err.message : t("draft_repair_failed_toast"), "error");
        return false;
      }
    },
    [view, current, projectName, episode, adoptView, onSettled, pushToast, t],
  );

  // Tracked repairs become terminal: notify results and refetch callers (adoption changed formal content, otherwise drafts persisted). Report each task once.
  const reportedRepairId = useRef<string | null>(null);
  useEffect(() => {
    if (watchedRepair == null || isOccupyingStatus(watchedRepair.status)) return;
    if (reportedRepairId.current === watchedRepair.task_id) return;
    reportedRepairId.current = watchedRepair.task_id;
    if (watchedRepair.status === "succeeded") {
      const result = (watchedRepair.result ?? {}) as Partial<DraftRepairTaskResult>;
      if (result.adopted) {
        pushToast(t("draft_adopted_toast"), "success");
      } else {
        pushToast(t("draft_repaired_with_violations_toast", { count: result.violation_count ?? 0 }), "warning");
      }
    } else if (watchedRepair.status === "failed") {
      pushToast(watchedRepair.error_message || t("draft_repair_failed_toast"), "error");
    }
    onSettled();
  }, [watchedRepair, onSettled, pushToast, t]);

  /** Discard the displayed draft revision; `true` allows confirmation closure. If changed elsewhere, retain it and load latest for users to reconsider. */
  const discard = useCallback(async (): Promise<boolean> => {
    if (view == null) return false;
    setDiscarding(true);
    try {
      await API.discardEpisodeDraft(projectName, episode, view.doc_type, current?.revision ?? view.revision);
      pushToast(t("draft_discarded_toast"), "success");
      onSettled();
      return true;
    } catch (err) {
      if (diagnosticCode(err) === "revision_conflict") {
        adoptView(view);
        pushToast(t("draft_conflict_toast"), "warning");
        onSettled();
        return true;
      }
      pushToast(err instanceof Error && err.message ? err.message : t("draft_discard_failed_toast"), "error");
      onSettled();
      return false;
    } finally {
      setDiscarding(false);
    }
  }, [view, current, projectName, episode, adoptView, onSettled, pushToast, t]);

  return {
    content: current?.edited ?? null,
    setContent,
    dirty,
    outdated,
    saving,
    discarding,
    repairing,
    save,
    repair,
    discard,
    reloadLatest,
  };
}
