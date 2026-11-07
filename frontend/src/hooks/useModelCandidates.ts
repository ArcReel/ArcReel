import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { ModelCandidatesResponse } from "@/types/system";

/**
 * Task-bucket candidate loading (docs/adr/0054): global/project settings consume the same global data;
 * loading/failure explanations live here, callers receive results only.
 *
 * Three disjoint states distinguish silent section degradation from explicit errors:
 *   candidates=null, error=false  → still loading (or not triggered), retaining silent degradation
 *   candidates!=null              → success, including empty buckets without errors
 *   error=true                    → fetch failed; callers render errors/retry entries
 */
export interface ModelCandidatesState {
  candidates: ModelCandidatesResponse | null;
  error: boolean;
  /** Retry in flight; disables retry buttons so slow responses do not leave clicks without feedback. */
  retrying: boolean;
  /** Fetch once; component unmount and subsequent calls invalidate in-flight requests. */
  reload: () => Promise<void>;
}

/**
 * Never fetch automatically on mount: the two callers trigger differently. Project settings fetch once on mount; globals combine it
 * with post-save page-wide refetches. Callers choose when to `reload()`; hooks only guarantee no stale-response writes.
 *
 * Candidate responses include provider/model names rendered by Accept-Language, so `reload` identity changes with language:
 * callers commonly include it in effect dependencies, automatically refetching after switches without refetching entire forms (which clears
 * unsaved edits).
 */
export function useModelCandidates(): ModelCandidatesState {
  const { i18n } = useTranslation();
  const language = i18n.language;
  const [candidates, setCandidates] = useState<ModelCandidatesResponse | null>(null);
  const [error, setError] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    // Taking-over requests rotate controllers (docs/standards/frontend.md): retries may trigger before previous
    // requests return, making old responses stale on arrival.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRetrying(true);
    try {
      const next = await API.getModelCandidates({ signal: controller.signal });
      // State-write checkpoint after network awaits: abort may follow already-resolved responses.
      if (controller.signal.aborted) return;
      setCandidates(next);
      setError(false);
    } catch {
      if (controller.signal.aborted) return;
      setCandidates(null);
      setError(true);
    } finally {
      // Superseded requests yield: never reset shared state after invalidation, or newly activated successor in-flight markers disappear.
      if (!controller.signal.aborted) setRetrying(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- language is unused in function bodies, changing reload identity on language switches only to trigger caller effects
  }, [language]);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { candidates, error, retrying, reload };
}
