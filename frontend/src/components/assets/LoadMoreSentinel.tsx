import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Start loading the next page this far before the viewport bottom, so content usually arrives before reaching the end. */
const PREFETCH_MARGIN = "0px 0px 480px 0px";

/**
 * List-end sentinel: call `onReach` to load the next page near the scroll-container viewport.
 * Observe again whenever item count changes, continuing until the first screen fills or there are no more pages.
 * On load failure, show the error and Retry instead of triggering automatically.
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
  /** Pass a stable reference. */
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
