import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { useRetainWhile } from "@/components/shared/edit-unit/edit-unit-retention";
import { API, ApiRequestError } from "@/api";
import type { ScriptOverwrite, ScriptReviewState } from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";

type ScriptReviewContent = NonNullable<ScriptReviewState["content"]>;

function scriptReviewErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "";
}

function overwriteFromError(err: unknown): ScriptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const overwrite = (diagnostic as { script_overwrite?: unknown }).script_overwrite;
  return overwrite != null && typeof overwrite === "object" ? (overwrite as ScriptOverwrite) : null;
}

function diagnosticCode(err: unknown): string | null {
  if (!(err instanceof ApiRequestError)) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const code = (diagnostic as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

function isScriptPlanConfirmedError(err: unknown): boolean {
  return err instanceof ApiRequestError && err.status === 409 && diagnosticCode(err) === "script_plan_confirmed";
}

function isRevisionConflictError(err: unknown): boolean {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return false;
  const code = diagnosticCode(err);
  if (code === "revision_conflict" || code === "conflict") return true;
  if (code != null) return false;
  if (overwriteFromError(err) != null) return false;
  if (err.detail != null && typeof err.detail === "object") return false;
  return true;
}

function isReadOnly(state: ScriptReviewState): boolean {
  return state.status === "confirmed" || state.status === "no_script_plan";
}

function isDirty(draft: unknown, serverContent: unknown): boolean {
  if (draft == null) return false;
  return JSON.stringify(draft) !== JSON.stringify(serverContent);
}

function clone<TDraft>(content: TDraft | null): TDraft | null {
  return content ? structuredClone(content) : null;
}

interface ScriptReviewDraftOptions<TDraft extends ScriptReviewContent> {
  projectName: string;
  episode: number;
  selectContent: (state: ScriptReviewState) => TDraft | null;
  onConfirmed: () => void;
}

interface ScriptReviewDraftHandle<TDraft extends ScriptReviewContent> {
  state: ScriptReviewState | null;
  draft: TDraft | null;
  setDraft: React.Dispatch<React.SetStateAction<TDraft | null>>;
  dirty: boolean;
  outdated: boolean;
  loading: boolean;
  loadError: { message: string } | null;
  saving: boolean;
  confirming: boolean;
  busy: boolean;
  retry: () => void;
  refresh: () => void;
  reloadLatest: () => void;
  save: () => Promise<boolean>;
  confirm: (options?: { overwriteRevision?: string }) => Promise<boolean>;
}

export function useScriptReviewDraft<TDraft extends ScriptReviewContent>({
  projectName,
  episode,
  selectContent,
  onConfirmed,
}: ScriptReviewDraftOptions<TDraft>): ScriptReviewDraftHandle<TDraft> {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const scriptPlanRevision = useAppStore((s) => s.getEntityRevision(`script_plan:episode_${episode}`));
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_script_plan`));
  const capabilitiesRevision = useCapabilitiesStore((s) => s.revision);

  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [draft, setDraft] = useState<TDraft | null>(null);
  const [baseFingerprint, setBaseFingerprint] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ message: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const serverContent = state ? selectContent(state) : null;
  const dirty = useMemo(() => isDirty(draft, serverContent), [draft, serverContent]);
  const outdated =
    conflict ||
    (state != null && !isReadOnly(state) && baseFingerprint != null && state.fingerprint !== baseFingerprint);
  const busy = saving || confirming;

  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  const loadControllerRef = useRef<AbortController | null>(null);
  const loadInFlightRef = useRef(false);
  const loadEpochRef = useRef(0);
  const forceAdoptRef = useRef(false);

  const adopt = useCallback(
    (next: ScriptReviewState, startEpoch?: number) => {
      const abandonedLoad = loadInFlightRef.current;
      const staleAgainstLoad = startEpoch !== undefined && loadEpochRef.current !== startEpoch;
      loadControllerRef.current?.abort();
      loadInFlightRef.current = false;
      setConflict(false);
      setState(next);
      setDraft(clone(selectContent(next)));
      setBaseFingerprint(next.fingerprint);
      if (abandonedLoad || staleAgainstLoad) setReloadNonce((n) => n + 1);
    },
    [selectContent],
  );

  const retry = useCallback(() => {
    setLoadError(null);
    setLoading(true);
    setReloadNonce((n) => n + 1);
  }, []);

  const refresh = useCallback(() => {
    setReloadNonce((n) => n + 1);
  }, []);

  const reloadLatest = useCallback(() => {
    forceAdoptRef.current = true;
    setReloadNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    loadControllerRef.current = controller;
    loadInFlightRef.current = true;
    loadEpochRef.current += 1;
    const hadResponse = state != null;
    const hasContent = draft != null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 首屏拉取前须同步点亮加载态，已有响应时静默刷新
    if (!hadResponse) setLoading(true);
    API.getScriptReview(projectName, episode, { signal })
      .then((next) => {
        if (signal.aborted) return;
        const forced = forceAdoptRef.current;
        forceAdoptRef.current = false;
        setLoadError(null);
        setState(next);
        if (!forced && dirtyRef.current && isReadOnly(next)) setConflict(true);
        if (forced || !dirtyRef.current) {
          dirtyRef.current = false;
          setConflict(false);
          setDraft(clone(selectContent(next)));
          setBaseFingerprint(next.fingerprint);
        }
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        if (forceAdoptRef.current) {
          forceAdoptRef.current = false;
          pushToast(scriptReviewErrorMessage(err) || t("dashboard:review_load_failed"), "error");
        }
        if (!hasContent) setLoadError({ message: scriptReviewErrorMessage(err) });
      })
      .finally(() => {
        if (!signal.aborted) {
          loadInFlightRef.current = false;
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state/draft 仅用于决定加载态与错误态分支，加入 deps 会在每次刷新后重新拉取造成循环
  }, [projectName, episode, scriptPlanRevision, draftRevision, capabilitiesRevision, reloadNonce, selectContent]);

  const handleSaveError = useCallback(
    (err: unknown) => {
      if (isScriptPlanConfirmedError(err)) {
        setConflict(false);
        setReloadNonce((n) => n + 1);
        pushToast(scriptReviewErrorMessage(err) || t("dashboard:save_failed", { message: "" }), "error");
        return;
      }
      if (isRevisionConflictError(err)) {
        setConflict(true);
        setReloadNonce((n) => n + 1);
        pushToast(scriptReviewErrorMessage(err) || t("dashboard:review_conflict_toast"), "warning");
        return;
      }
      pushToast(scriptReviewErrorMessage(err) || t("dashboard:save_failed", { message: "" }), "error");
    },
    [pushToast, t],
  );

  const save = useCallback(async () => {
    if (!draft || (state != null && isReadOnly(state))) return false;
    setSaving(true);
    const startEpoch = loadEpochRef.current;
    try {
      const next = await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint);
      adopt(next, startEpoch);
      const remaining = next.blocking_issues?.length ?? 0;
      if (remaining > 0) {
        pushToast(t("dashboard:review_saved_with_blocking_issues", { count: remaining }), "warning");
      } else {
        pushToast(t("dashboard:review_saved"), "success");
      }
      return true;
    } catch (err) {
      handleSaveError(err);
      return false;
    } finally {
      setSaving(false);
    }
  }, [draft, state, baseFingerprint, projectName, episode, adopt, handleSaveError, pushToast, t]);

  const discarding = useLeaveGuard({ dirty, saving: busy, save, discard: reloadLatest });
  useRetainWhile((dirty || busy) && !discarding);

  const confirm = useCallback(async (options: { overwriteRevision?: string } = {}): Promise<boolean> => {
    setConfirming(true);
    try {
      if (dirty && draft) {
        const startEpoch = loadEpochRef.current;
        let saved: ScriptReviewState;
        try {
          saved = await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint);
        } catch (err) {
          handleSaveError(err);
          return false;
        }
        adopt(saved, startEpoch);
        const remaining = saved.blocking_issues?.length ?? 0;
        if (remaining > 0) {
          pushToast(t("dashboard:review_saved_with_blocking_issues", { count: remaining }), "warning");
          return false;
        }
      }
      const confirmEpoch = loadEpochRef.current;
      adopt(await API.confirmScriptReview(projectName, episode, options), confirmEpoch);
      onConfirmed();
      return true;
    } catch (err) {
      const overwrite = overwriteFromError(err);
      if (overwrite) {
        setState((prev) => (prev ? { ...prev, script_overwrite: overwrite } : prev));
      } else if (err instanceof ApiRequestError && (err.status === 422 || err.status === 409)) {
        setReloadNonce((n) => n + 1);
      }
      pushToast(scriptReviewErrorMessage(err) || t("dashboard:review_confirm_failed"), "error");
      return false;
    } finally {
      setConfirming(false);
    }
  }, [dirty, draft, baseFingerprint, projectName, episode, adopt, handleSaveError, onConfirmed, pushToast, t]);

  return {
    state,
    draft,
    setDraft,
    dirty,
    outdated,
    loading,
    loadError,
    saving,
    confirming,
    busy,
    retry,
    refresh,
    reloadLatest,
    save,
    confirm,
  };
}
