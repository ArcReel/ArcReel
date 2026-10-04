import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/** 距离可视区底部还有这么远时就开始加载下一页，滚到底时内容多半已经到了。 */
const PREFETCH_MARGIN = "0px 0px 480px 0px";

/**
 * 列表末尾的哨兵：进入滚动容器的可视区附近时调用 `onReach` 加载下一页。
 * 每次条目数变化都重新观察一次，首屏没有填满时会接着加载，直到填满或没有下一页。
 * 加载失败时显示错误与「重试」，不再自动触发。
 */
export function LoadMoreSentinel({
  hasMore,
  loading,
  error,
  itemCount,
  onReach,
  onRetry,
}: {
  hasMore: boolean;
  loading: boolean;
  error: string | null;
  itemCount: number;
  /** 需传稳定引用。 */
  onReach: () => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation("assets");
  const ref = useRef<HTMLDivElement>(null);
  const armed = hasMore && !loading && !error;

  useEffect(() => {
    const node = ref.current;
    if (!armed || !node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onReach();
      },
      { rootMargin: PREFETCH_MARGIN },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [armed, itemCount, onReach]);

  if (!hasMore && !error) return null;
  return (
    <div ref={ref} className="flex min-h-12 items-center justify-center gap-3 text-sm text-muted-foreground">
      {error ? (
        <>
          <span role="alert">{t("load_more_failed", { message: error })}</span>
          <Button variant="outline" size="sm" onClick={onRetry}>
            {t("retry")}
          </Button>
        </>
      ) : loading ? (
        <>
          <Loader2 aria-hidden className="size-4 animate-spin" />
          <span>{t("loading")}</span>
        </>
      ) : null}
    </div>
  );
}
