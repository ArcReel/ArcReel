import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import type { ScriptOverwrite, ScriptReviewState } from "@/types";
import { useAppStore } from "@/stores/app-store";

/** Panel-editable drafts: one `ScriptReviewState.content` variant. */
type ScriptReviewContent = NonNullable<ScriptReviewState["content"]>;

function scriptReviewErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "";
}

/** Extract server-listed overwrite consequences when confirmation refuses existing formal scripts without overwrite consent. */
function overwriteFromError(err: unknown): ScriptOverwrite | null {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const overwrite = (diagnostic as { script_overwrite?: unknown }).script_overwrite;
  return overwrite != null && typeof overwrite === "object" ? (overwrite as ScriptOverwrite) : null;
}

/** Saving refused because script plans are confirmed and readonly. */
function isScriptPlanConfirmedError(err: unknown): boolean {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return false;
  const diagnostic = err.diagnostic;
  return diagnostic != null && typeof diagnostic === "object" && (diagnostic as { code?: unknown }).code === "script_plan_confirmed";
}

/** Confirmed script plans without repair drafts are readonly; local unsaved edits have nowhere to persist, so external refreshes adopt server content directly. */
function isReadOnly(state: ScriptReviewState): boolean {
  return state.status === "confirmed" && state.quarantine == null;
}

/** Unsaved content edits: compare serialized forms; drafts clone server content with stable key order. */
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
  /**
   * Extract panel-editable content from server review states; null for variants outside this panel.
   * Requires stable references (module functions or `useCallback`), used in fetch-effect dependencies.
   */
  selectContent: (state: ScriptReviewState) => TDraft | null;
  /** Panel-specific successful-confirmation handling (including success notices); this hook centralizes failure notices. */
  onConfirmed: () => void;
}

interface ScriptReviewDraftHandle<TDraft extends ScriptReviewContent> {
  /** Latest fetched server review state; null before any response. */
  state: ScriptReviewState | null;
  draft: TDraft | null;
  setDraft: React.Dispatch<React.SetStateAction<TDraft | null>>;
  /** Drafts differ from their server-content baseline, indicating unsaved edits. */
  dirty: boolean;
  loading: boolean;
  loadError: { message: string } | null;
  saving: boolean;
  confirming: boolean;
  /** Save/confirm requests in flight: callers lock edit controls so echoed responses cannot overwrite edits made after requests. */
  busy: boolean;
  retry: () => void;
  /** Silently refetch server review states (adopted/discarded drafts change both formal content and draft information). */
  refresh: () => void;
  save: () => Promise<void>;
  /**
   * Confirm and convert entirely into formal scripts. Existing formal scripts require consented `overwriteRevision`; missing/stale revisions refused by
   * servers write current server-listed consequences into `state.script_overwrite`, switching panels to danger confirmation.
   * Failures are handled locally (toast), never reraised; return success so callers decide whether to close confirmations.
   */
  confirm: (options?: { overwriteRevision?: string }) => Promise<boolean>;
}

/**
 * Draft state machine for script_plan → prompt_authoring confirmation panels: fetch / external refresh / dirty state / adopt / save / confirm.
 * Shared by `ScriptReviewGate` (drama, narration) and `ReferenceScriptPlanPreviewPanel` (reference_video);
 * panels retain their own presentation and specific interactions only.
 */
export function useScriptReviewDraft<TDraft extends ScriptReviewContent>({
  projectName,
  episode,
  selectContent,
  onConfirmed,
}: ScriptReviewDraftOptions<TDraft>): ScriptReviewDraftHandle<TDraft> {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_script_plan`));

  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [draft, setDraft] = useState<TDraft | null>(null);
  // Save baseline fingerprints bind **server content underlying current drafts**, advancing only when drafts adopt server content.
  // Never use state.fingerprint: external refreshes update state but retain unsaved drafts; submitting
  // refreshed baselines makes OCC silently permit overwriting others' changes.
  const [baseFingerprint, setBaseFingerprint] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ message: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const serverContent = state ? selectContent(state) : null;
  const dirty = useMemo(() => isDirty(draft, serverContent), [draft, serverContent]);
  const busy = saving || confirming;

  // Mirror dirty into refs for latest fetch-effect reads without dirty deps, which would refetch on every edit.
  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  // Fetch controllers/in-flight flags support adopt invalidation/follow-up decisions.
  const loadControllerRef = useRef<AbortController | null>(null);
  const loadInFlightRef = useRef(false);

  // Adopt server content as deep-cloned drafts without shared references after deliberate save/confirm actions,
  // always replacing local drafts. setDraft takes values, never updaters reading/writing refs, preserving purity.
  //
  // Invalidate all preadoption fetches: they may omit this write. After adoption sets dirtyRef false,
  // late old contents would rewrite drafts/baseFingerprint, reverting drafts and causing next-save OCC conflicts.
  // External-event fetches may have consumed Agent script_plan revisions with no later trigger,
  // losing updates if discarded. When actual in-flight fetches invalidate, refetch once to recover them.
  const adopt = useCallback(
    (next: ScriptReviewState) => {
      const abandonedLoad = loadInFlightRef.current;
      loadControllerRef.current?.abort();
      loadInFlightRef.current = false;
      setState(next);
      setDraft(clone(selectContent(next)));
      setBaseFingerprint(next.fingerprint);
      if (abandonedLoad) setReloadNonce((n) => n + 1);
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

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    loadControllerRef.current = controller;
    loadInFlightRef.current = true;
    // After any response, empty or populated, revision refetches stay silent without loading flashes;
    const hadResponse = state != null;
    // failures retain real visible content silently, entering error states only without contents (initial/empty states).
    const hasContent = draft != null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetches synchronously activate loading; existing-response refreshes stay silent
    if (!hadResponse) setLoading(true);
    API.getScriptReview(projectName, episode, { signal })
      .then((next) => {
        // Abort after response resolution: recheck before writes, preventing departed requests persisting.
        if (signal.aborted) return;
        setLoadError(null);
        setState(next);
        // External mount/Agent revision refreshes adopt server content without unsaved edits or when confirmed readonly;
        // otherwise update server state only, retaining drafts. dirtyRef reads inside effects are safe, outside render.
        if (!dirtyRef.current || isReadOnly(next)) {
          setDraft(clone(selectContent(next)));
          setBaseFingerprint(next.fingerprint);
        }
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        // Without real content, initial/empty-state refresh failures show errors, distinct from empty states, with retries;
        // populated silent failures retain views unchanged.
        if (!hasContent) setLoadError({ message: scriptReviewErrorMessage(err) });
      })
      .finally(() => {
        // Yield cleanup to successors: invalidated requests never touch in-flight/loading flags, avoiding interrupted new loads.
        if (!signal.aborted) {
          loadInFlightRef.current = false;
          setLoading(false);
        }
      });
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state/draft choose loading/error branches only; deps would refetch after every refresh, looping
  }, [projectName, episode, draftRevision, reloadNonce, selectContent]);

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      adopt(await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint));
      pushToast(t("dashboard:review_saved"), "success");
    } catch (err) {
      // Episodes confirmed during edits refetch, making panels readonly.
      if (isScriptPlanConfirmedError(err)) setReloadNonce((n) => n + 1);
      pushToast(scriptReviewErrorMessage(err) || t("dashboard:save_failed", { message: "" }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, baseFingerprint, projectName, episode, adopt, pushToast, t]);

  const confirm = useCallback(async (options: { overwriteRevision?: string } = {}): Promise<boolean> => {
    setConfirming(true);
    try {
      if (dirty && draft) {
        adopt(await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint));
      }
      adopt(await API.confirmScriptReview(projectName, episode, options));
      onConfirmed();
      return true;
    } catch (err) {
      const overwrite = overwriteFromError(err);
      if (overwrite) {
        setState((prev) => (prev ? { ...prev, script_overwrite: overwrite } : prev));
      }
      pushToast(scriptReviewErrorMessage(err) || t("dashboard:review_confirm_failed"), "error");
      return false;
    } finally {
      setConfirming(false);
    }
  }, [dirty, draft, baseFingerprint, projectName, episode, adopt, onConfirmed, pushToast, t]);

  return {
    state,
    draft,
    setDraft,
    dirty,
    loading,
    loadError,
    saving,
    confirming,
    busy,
    retry,
    refresh,
    save,
    confirm,
  };
}
