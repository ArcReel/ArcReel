import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import type { ScriptOverwrite, ScriptReviewState } from "@/types";
import { useAppStore } from "@/stores/app-store";

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

function isScriptPlanConfirmedError(err: unknown): boolean {
  if (!(err instanceof ApiRequestError) || err.status !== 409) return false;
  const diagnostic = err.diagnostic;
  return diagnostic != null && typeof diagnostic === "object" && (diagnostic as { code?: unknown }).code === "script_plan_confirmed";
}

function isReadOnly(state: ScriptReviewState): boolean {
  return state.status === "confirmed" && state.quarantine == null;
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
  loading: boolean;
  loadError: { message: string } | null;
  saving: boolean;
  confirming: boolean;
  busy: boolean;
  retry: () => void;
  refresh: () => void;
  save: () => Promise<void>;
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
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_script_plan`));

  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [draft, setDraft] = useState<TDraft | null>(null);
  const [baseFingerprint, setBaseFingerprint] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ message: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const serverContent = state ? selectContent(state) : null;
  const dirty = useMemo(() => isDirty(draft, serverContent), [draft, serverContent]);
  const busy = saving || confirming;

  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  const loadControllerRef = useRef<AbortController | null>(null);
  const loadInFlightRef = useRef(false);

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
    const hadResponse = state != null;
    const hasContent = draft != null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 首屏拉取前须同步点亮加载态，已有响应时静默刷新
    if (!hadResponse) setLoading(true);
    API.getScriptReview(projectName, episode, { signal })
      .then((next) => {
        if (signal.aborted) return;
        setLoadError(null);
        setState(next);
        if (!dirtyRef.current || isReadOnly(next)) {
          setDraft(clone(selectContent(next)));
          setBaseFingerprint(next.fingerprint);
        }
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
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
  }, [projectName, episode, draftRevision, reloadNonce, selectContent]);

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      adopt(await API.saveScriptReviewContent(projectName, episode, draft, baseFingerprint));
      pushToast(t("dashboard:review_saved"), "success");
    } catch (err) {
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
