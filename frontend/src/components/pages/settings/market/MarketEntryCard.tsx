import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
} from "@/components/shared/darkroom-tokens";
import type { MarketEntry, MarketEntryAggregate, MarketSourceKind } from "@/types";
import { MarketEntryStats } from "./MarketEntryStats";
import { MarketInstallBadges } from "./MarketInstallBadges";

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

export function EntryIcon({ entry }: { entry: MarketEntry }) {
  const url = useEntryIconUrl(entry);
  if (url !== null) {
    return (
      <img
        src={url}
        alt=""
        width={ICON_SIZE}
        height={ICON_SIZE}
        className="shrink-0 rounded-lg object-contain"
      />
    );
  }
  return (
    <div
      aria-hidden
      className="flex shrink-0 items-center justify-center rounded-lg border border-border/50 font-editorial text-muted-foreground"
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
export function SourceChip({ name, kind }: { name: string; kind: MarketSourceKind | null }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 truncate rounded-full border border-border/50 bg-card/50 px-2 py-[1px] font-mono text-[10px] text-muted-foreground">
      {kind === "official" && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />}
      <span className="truncate">{name}</span>
    </span>
  );
}

/**
 * 市场条目卡片：2:1 图区（icon 或首字母占位 + 两轴徽标 + 媒体类型标签）、名称、作者与版本、两行描述、源片（及官方服务的
 * 安装量与评分）与主按钮。
 * 主按钮：未安装「安装」、可更新「更新」均打开确认弹窗；已是最新「已安装」直接打开端点。
 * 当前应用版本不满足 `min_app_version` 时整卡降透明并标出版本要求。
 */
export function MarketEntryCard({
  entry,
  sourceName,
  sourceKind,
  appVersion,
  aggregate = null,
  onOpen,
  onInstalledOpen,
}: {
  entry: MarketEntry;
  /** 源的当前显示名；源列表里找不到时退回条目自带的显示名。 */
  sourceName: string;
  sourceKind: MarketSourceKind | null;
  appVersion: string | null;
  /** 官方服务开启且取到聚合时才有；缺失即不显示数字。 */
  aggregate?: MarketEntryAggregate | null;
  onOpen?: () => void;
  onInstalledOpen?: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const unmet = !entry.min_app_version_satisfied && entry.min_app_version !== null;
  const { installation } = entry;
  const current = installation?.state === "current";

  return (
    <article
      aria-label={entry.name}
      className={`relative flex flex-col overflow-hidden rounded-xl border border-border text-left transition-[transform,border-color] hover:border-input motion-safe:hover:-translate-y-0.5 ${
        unmet ? "opacity-60" : ""
      }`}
      style={CARD_STYLE}
    >
      {/* 版本不满足时不铺覆盖层，禁用的覆盖按钮仍会拦下版本徽标的悬停提示。 */}
      {!unmet && (
        <button
          type="button"
          aria-label={entry.name}
          onClick={onOpen}
          className="absolute inset-0 z-10 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      )}
      <div
        className="relative flex aspect-[2/1] items-center justify-center border-b border-border/50"
        style={{ background: "oklch(0.14 0.010 265 / 0.6)" }}
      >
        <EntryIcon entry={entry} />
        {installation && (
          <div className="absolute left-2 top-2">
            <MarketInstallBadges state={installation.state} modified={installation.modified} />
          </div>
        )}
        {(entry.media_type === "image" || entry.media_type === "video") && (
          <span className="absolute right-2 top-2 rounded-sm border border-border/50 bg-card/55 px-1 py-px font-mono text-[9px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
            {t(entry.media_type === "image" ? "media_type_image" : "media_type_video")}
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col p-3">
        <h3 className="truncate text-[13.5px] font-medium text-foreground">{entry.name}</h3>
        <div className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
          {entry.author} · v{entry.version}
        </div>
        <p className="mt-1.5 line-clamp-2 flex-1 text-[11.5px] leading-[1.5] text-muted-foreground">
          {entry.description}
        </p>
        <div className="mt-2 flex min-w-0 items-center justify-between gap-2">
          <SourceChip name={sourceName} kind={sourceKind} />
          {aggregate && <MarketEntryStats aggregate={aggregate} />}
        </div>
        <button
          type="button"
          disabled={unmet}
          onClick={current ? onInstalledOpen : onOpen}
          className={`relative z-20 mt-3 self-end ${current ? GHOST_BTN_CLS : ACCENT_BTN_SM_CLS}`}
          style={current ? undefined : ACCENT_BUTTON_STYLE}
        >
          {t(current ? "market_installed" : installation ? "market_update" : "market_install")}
        </button>
        {unmet && (
          <div className="mt-3 flex items-center">
            <span
              title={appVersion === null ? undefined : t("market_current_app_version", { version: appVersion })}
              className="inline-flex shrink-0 items-center whitespace-nowrap rounded-sm border border-border/50 bg-card/55 px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-muted-foreground"
            >
              {t("market_requires_app", { version: entry.min_app_version })}
            </span>
          </div>
        )}
      </div>
    </article>
  );
}
