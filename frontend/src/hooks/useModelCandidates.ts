import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { ModelCandidatesResponse } from "@/types/system";

export interface ModelCandidatesState {
  candidates: ModelCandidatesResponse | null;
  error: boolean;
  retrying: boolean;
  reload: () => Promise<void>;
}

export function useModelCandidates(): ModelCandidatesState {
  const { i18n } = useTranslation();
  const language = i18n.language;
  const [candidates, setCandidates] = useState<ModelCandidatesResponse | null>(null);
  const [error, setError] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRetrying(true);
    try {
      const next = await API.getModelCandidates({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setCandidates(next);
      setError(false);
    } catch {
      if (controller.signal.aborted) return;
      setCandidates(null);
      setError(true);
    } finally {
      if (!controller.signal.aborted) setRetrying(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- language 不进函数体，只用来在语言切换时换掉 reload 的标识，驱动调用点的 effect 重取
  }, [language]);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { candidates, error, retrying, reload };
}
