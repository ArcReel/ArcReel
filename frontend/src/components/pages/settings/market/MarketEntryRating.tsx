import { useId, useState } from "react";
import { Star } from "lucide-react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg } from "@/utils/async";

const STARS = [1, 2, 3, 4, 5] as const;

/**
 * 1–5 星评分，经本地服务端代理到官方服务，可随时改评。只有本地有安装记录时可用；
 * 官方服务没有本实例的安装记录等拒绝原因原样显示在控件旁。
 */
export function MarketEntryRating({
  sourceId,
  slug,
  installed,
  busy,
  onBusyChange,
  onRated,
}: {
  sourceId: number;
  slug: string;
  installed: boolean;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onRated?: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const labelId = useId();
  const hintId = useId();
  const [stars, setStars] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (value: number) => {
    if (!installed || busy) return;
    onBusyChange(true);
    setResult(null);
    try {
      await API.rateMarketEntry(sourceId, slug, value);
      setStars(value);
      setResult({ ok: true, text: t("market_rate_saved") });
      onRated?.();
    } catch (e) {
      setResult({ ok: false, text: t("market_rate_failed", { message: errMsg(e) }) });
    } finally {
      onBusyChange(false);
    }
  };

  const lit = installed ? (hovered ?? stars ?? 0) : 0;

  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
      <span id={labelId} className="text-muted-foreground">
        {t("market_rate_label")}
      </span>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        aria-describedby={installed ? undefined : hintId}
        className="flex items-center"
      >
        {STARS.map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={stars === value}
            aria-label={t("market_rate_star", { stars: value })}
            disabled={!installed || busy}
            onMouseEnter={() => setHovered(value)}
            onMouseLeave={() => setHovered(null)}
            onClick={() => void submit(value)}
            className="rounded-sm p-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Star
              className={cn("size-4 transition-colors", value <= lit ? "fill-primary text-primary" : "text-muted-foreground")}
              aria-hidden
            />
          </button>
        ))}
      </div>
      {!installed && (
        <span id={hintId} className="text-muted-foreground">
          {t("market_rate_install_first")}
        </span>
      )}
      {result && (
        <span role={result.ok ? "status" : "alert"} className={result.ok ? "text-good" : "text-destructive"}>
          {result.text}
        </span>
      )}
    </div>
  );
}
