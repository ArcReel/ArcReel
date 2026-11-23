import { useEffect, useState } from "react";
import { API } from "@/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { CatalogEntryHit, MediaType } from "@/types";
import { errMsg } from "@/utils/async";

type SearchResult =
  | { status: "ready"; key: string; entries: CatalogEntryHit[] }
  | { status: "error"; key: string; message: string };

export interface CatalogEntrySearch {
  entries: CatalogEntryHit[] | null;
  error: string | null;
  settled: boolean;
  retry: () => void;
}

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
