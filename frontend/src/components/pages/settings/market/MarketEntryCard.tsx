import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { CARD_STYLE, posterGridStyle } from "@/components/ui/darkroom-tokens";
import type { MarketEntry, MarketSourceKind } from "@/types";

const ICON_SIZE = 48;

/** 经后端代理取 icon 转成 object URL；取不到时返回 null，由调用方显示首字母占位。 */
function useEntryIconUrl(entry: MarketEntry): string | null {
  const [url, setUrl] = useState<string | null>(null);
  const { source_id: sourceId, slug, version, icon } = entry;

  useEffect(() => {
    if (icon === null) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    API.getMarketEntryIcon(sourceId, slug, version, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        // icon 不可用不影响浏览：保持首字母占位。
      });
    return () => {
      controller.abort();
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
      setUrl(null);
    };
  }, [icon, slug, sourceId, version]);

  return url;
}

function EntryIcon({ entry }: { entry: MarketEntry }) {
  const url = useEntryIconUrl(entry);
  if (url !== null) {
    return (
      <img
        src={url}
        alt=""
        width={ICON_SIZE}
        height={ICON_SIZE}
        className="shrink-0 rounded-[9px] object-contain"
      />
    );
  }
  return (
    <div
      aria-hidden
      className="flex shrink-0 items-center justify-center rounded-[9px] border border-hairline-soft font-editorial text-text-4"
      style={{
        width: ICON_SIZE,
        height: ICON_SIZE,
        fontSize: ICON_SIZE * 0.48,
        background:
          "linear-gradient(180deg, oklch(0.22 0.011 265 / 0.7), oklch(0.17 0.010 265 / 0.7))",
      }}
    >
      {entry.name.trim().charAt(0).toUpperCase()}
    </div>
  );
}

/** 标识条目来源的源片；官方源带强调色圆点。 */
function SourceChip({ name, kind }: { name: string; kind: MarketSourceKind | null }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 truncate rounded-full border border-hairline-soft bg-bg-grad-a/50 px-2 py-[1px] font-mono text-[10px] text-text-3">
      {kind === "official" && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-2" aria-hidden />}
      <span className="truncate">{name}</span>
    </span>
  );
}

/**
 * 市场条目卡片：2:1 图区（icon 或首字母占位）、名称、作者与版本、两行描述与源片。
 * 当前应用版本不满足 `min_app_version` 时整卡降透明并标出版本要求。
 */
export function MarketEntryCard({
  entry,
  sourceName,
  sourceKind,
  appVersion,
}: {
  entry: MarketEntry;
  /** 源的当前显示名；源列表里找不到时退回条目自带的显示名。 */
  sourceName: string;
  sourceKind: MarketSourceKind | null;
  appVersion: string | null;
}) {
  const { t } = useTranslation("dashboard");
  const unmet = !entry.min_app_version_satisfied && entry.min_app_version !== null;

  return (
    <article
      aria-label={entry.name}
      className={`relative flex flex-col overflow-hidden rounded-[12px] border border-hairline text-left transition-[transform,border-color] hover:border-hairline-strong motion-safe:hover:-translate-y-0.5 ${
        unmet ? "opacity-60" : ""
      }`}
      style={CARD_STYLE}
    >
      <div
        className="relative flex aspect-[2/1] items-center justify-center border-b border-hairline-soft"
        style={{ background: "oklch(0.14 0.010 265 / 0.6)" }}
      >
        <div aria-hidden className="absolute inset-0 opacity-[0.06]" style={posterGridStyle({ size: 20 })} />
        <EntryIcon entry={entry} />
      </div>
      <div className="flex flex-1 flex-col p-3">
        <h3 className="truncate text-[13.5px] font-medium text-text">{entry.name}</h3>
        <div className="mt-0.5 truncate text-[11.5px] text-text-4">
          {entry.author} · v{entry.version}
        </div>
        <p className="mt-1.5 line-clamp-2 flex-1 text-[11.5px] leading-[1.5] text-text-3">
          {entry.description}
        </p>
        <div className="mt-2">
          <SourceChip name={sourceName} kind={sourceKind} />
        </div>
        {unmet && (
          <div className="mt-3 flex items-center">
            <span
              title={appVersion === null ? undefined : t("market_current_app_version", { version: appVersion })}
              className="inline-flex shrink-0 items-center whitespace-nowrap rounded-[5px] border border-hairline-soft bg-bg-grad-a/55 px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-text-3"
            >
              {t("market_requires_app", { version: entry.min_app_version })}
            </span>
          </div>
        )}
      </div>
    </article>
  );
}
