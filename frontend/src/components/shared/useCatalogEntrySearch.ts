import { useEffect, useState } from "react";
import { API } from "@/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { CatalogEntryHit, MediaType } from "@/types";
import { errMsg } from "@/utils/async";

type SearchResult =
  | { status: "ready"; key: string; entries: CatalogEntryHit[] }
  | { status: "error"; key: string; message: string };

export interface CatalogEntrySearch {
  /** 最近一次返回的结果；还没有返回过时为 null。结果就绪前仍是上一次的结果。 */
  entries: CatalogEntryHit[] | null;
  /** 最近一次请求失败的原因；成功后清空。 */
  error: string | null;
  /** 结果对应当前搜索词与筛选：防抖未到期或请求未返回时为 false。 */
  settled: boolean;
  retry: () => void;
}

/**
 * 按名称与媒体类型搜索模型目录：服务端过滤，200ms 防抖，新请求发出时取消上一次。
 * 「从模型目录选择」面板与「添加模型」对话框共用。
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
