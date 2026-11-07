import { useEffect, useState } from "react";
import { API } from "@/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { CatalogEntryHit, MediaType } from "@/types";
import { errMsg } from "@/utils/async";

type SearchResult =
  | { status: "ready"; key: string; entries: CatalogEntryHit[] }
  | { status: "error"; key: string; message: string };

export interface CatalogEntrySearch {
  /** Latest returned result; null before any response. Retain the previous result until the new one is ready. */
  entries: CatalogEntryHit[] | null;
  /** Latest request-failure reason; clear after success. */
  error: string | null;
  /** Whether results match the current search/filter; false during debounce or while awaiting the response. */
  settled: boolean;
  retry: () => void;
}

/**
 * Search the model catalog by name/media type with server filtering, 200ms debounce and cancellation of the previous request on new dispatch.
 * Shared by Select from model catalog panels and Add model dialogs.
 */
export function useCatalogEntrySearch(query: string, mediaType: MediaType | undefined): CatalogEntrySearch {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<SearchResult | null>(null);
  const trimmed = query.trim();
  const debouncedQuery = useDebouncedValue(trimmed, 200);
  const requestKey = `${attempt}|${mediaType ?? "all"}|${debouncedQuery}`;

  useEffect(() => {
    const controller = new AbortController();
    API.searchCatalogEntries({ q: debouncedQuery, mediaType }, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setResult({ status: "ready", key: requestKey, entries: response.entries });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setResult({ status: "error", key: requestKey, message: errMsg(err) });
      });
    return () => controller.abort();
  }, [debouncedQuery, mediaType, requestKey]);

  const settled = result !== null && result.key === requestKey && debouncedQuery === trimmed;
  return {
    entries: result?.status === "ready" ? result.entries : null,
    error: result?.status === "error" && settled ? result.message : null,
    settled,
    retry: () => setAttempt((n) => n + 1),
  };
}
