import { useId, useState, type FormEvent, type KeyboardEvent } from "react";
import { ExternalLink, GripVertical, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { formatRelativeTime } from "@/utils/date-format";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  GHOST_BTN_CLS,
  ICON_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PillSwitch } from "@/components/ui/PillSwitch";
import type { MarketSourceInfo } from "@/types";
import { KICKER_ACCENT_CLS, SourceStatusDot } from "./market-source-status";

interface MarketSourcesDialogProps {
  open: boolean;
  onClose: () => void;
  sources: MarketSourceInfo[];
  onSourcesChange: (update: (current: MarketSourceInfo[]) => MarketSourceInfo[]) => void;
  refreshingIds: ReadonlySet<number>;
  refreshingAll: boolean;
  onRefresh: (id: number) => void;
  onRefreshAll: () => void;
}

function replaceSource(current: MarketSourceInfo[], next: MarketSourceInfo): MarketSourceInfo[] {
  return current.map((source) => (source.id === next.id ? next : source));
}

function moveItem<T>(items: T[], from: number, to: number): T[] {
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** 市场源管理弹窗：拖拽排序、改名、启停、单源刷新、删除与添加。 */
export function MarketSourcesDialog({
  open,
  onClose,
  sources,
  onSourcesChange,
  refreshingIds,
  refreshingAll,
  onRefresh,
  onRefreshAll,
}: MarketSourcesDialogProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const titleId = useId();
  const [dragId, setDragId] = useState<number | null>(null);

  const failToast = (err: unknown) =>
    pushToast(t("market_action_failed", { message: errMsg(err) }), "error");

  const commitOrder = async (next: MarketSourceInfo[]) => {
    const previous = sources;
    onSourcesChange(() => next);
    try {
      const { sources: saved } = await API.reorderMarketSources(next.map((source) => source.id));
      onSourcesChange(() => saved);
    } catch (err) {
      onSourcesChange(() => previous);
      failToast(err);
    }
  };

  const move = (id: number, to: number) => {
    const from = sources.findIndex((source) => source.id === id);
    if (from < 0 || to < 0 || to >= sources.length || from === to) return;
    void commitOrder(moveItem(sources, from, to));
  };

  const update = async (id: number, patch: { display_name?: string; is_enabled?: boolean }) => {
    const previous = sources.find((source) => source.id === id);
    if (previous) onSourcesChange((current) => replaceSource(current, { ...previous, ...patch }));
    try {
      const saved = await API.updateMarketSource(id, patch);
      onSourcesChange((current) => replaceSource(current, saved));
    } catch (err) {
      if (previous) onSourcesChange((current) => replaceSource(current, previous));
      failToast(err);
    }
  };

  const remove = async (id: number) => {
    try {
      await API.deleteMarketSource(id);
      onSourcesChange((current) => current.filter((source) => source.id !== id));
    } catch (err) {
      failToast(err);
    }
  };

  return (
    <GlassModal
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-full max-w-xl"
    >
      <div className="flex max-h-[80vh] flex-col">
        <div className="flex items-start justify-between gap-3 px-5 pt-5">
          <div className="min-w-0">
            <div className={KICKER_ACCENT_CLS}>Sources</div>
            <h3 id={titleId} className="mt-1 text-[16px] font-medium text-text">
              {t("market_manage_sources")}
            </h3>
            <p className="mt-0.5 text-[12px] text-text-3">{t("market_sources_desc")}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              className={GHOST_BTN_CLS}
              disabled={refreshingAll || !sources.some((source) => source.is_enabled)}
              onClick={onRefreshAll}
            >
              {refreshingAll ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              )}
              {t("market_refresh_all")}
            </button>
            <ModalCloseButton onClick={onClose} />
          </div>
        </div>

        <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-5 py-4">
          {sources.map((source, index) => (
            <SourceRow
              key={source.id}
              source={source}
              refreshing={refreshingIds.has(source.id)}
              dragging={dragId === source.id}
              onDragStart={() => setDragId(source.id)}
              onDrop={() => {
                if (dragId !== null) move(dragId, index);
                setDragId(null);
              }}
              onDragEnd={() => setDragId(null)}
              onMove={(offset) => move(source.id, index + offset)}
              onRename={(name) => void update(source.id, { display_name: name })}
              onToggle={() => void update(source.id, { is_enabled: !source.is_enabled })}
              onRefresh={() => onRefresh(source.id)}
              onDelete={() => void remove(source.id)}
            />
          ))}
        </ul>

        <div className="space-y-3 border-t border-hairline-soft px-5 py-4">
          <AddSourceForm
            onAdded={(added) => {
              onSourcesChange((current) => [...current, added]);
              pushToast(t("market_source_added", { name: added.display_name }), "success");
            }}
          />
        </div>
      </div>
    </GlassModal>
  );
}

interface SourceRowProps {
  source: MarketSourceInfo;
  refreshing: boolean;
  dragging: boolean;
  onDragStart: () => void;
  onDrop: () => void;
  onDragEnd: () => void;
  onMove: (offset: number) => void;
  onRename: (name: string) => void;
  onToggle: () => void;
  onRefresh: () => void;
  onDelete: () => void;
}

function SourceRow({
  source,
  refreshing,
  dragging,
  onDragStart,
  onDrop,
  onDragEnd,
  onMove,
  onRename,
  onToggle,
  onRefresh,
  onDelete,
}: SourceRowProps) {
  const { t, i18n } = useTranslation("dashboard");
  const switchLabelId = useId();
  // 编辑中的显示名；null 表示未在编辑，输入框直接显示已保存的名字
  const [draft, setDraft] = useState<string | null>(null);
  const official = source.kind === "official";
  const failed = source.status !== "ok" && source.status !== "never_fetched";

  const commitName = () => {
    const trimmed = draft?.trim();
    setDraft(null);
    if (trimmed && trimmed !== source.display_name) onRename(trimmed);
  };

  const onNameKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") event.currentTarget.blur();
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setDraft(null);
    }
  };

  const onGripKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      onMove(event.key === "ArrowUp" ? -1 : 1);
    }
  };

  const fetchedAt =
    formatRelativeTime(source.fetched_at, i18n.language) ?? t("market_never_fetched_time");

  return (
    <li
      draggable
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        onDrop();
      }}
      onDragEnd={onDragEnd}
      className={`flex items-center gap-2.5 rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 px-2.5 py-2 ${
        dragging ? "opacity-40" : ""
      }`}
    >
      <button
        type="button"
        className="shrink-0 cursor-grab rounded-[5px] text-text-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        aria-label={t("market_source_reorder", { name: source.display_name })}
        onKeyDown={onGripKeyDown}
      >
        <GripVertical className="h-4 w-4" aria-hidden />
      </button>
      <SourceStatusDot source={source} refreshing={refreshing} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <input
            className={`min-w-0 max-w-[260px] truncate rounded-[5px] border border-transparent bg-transparent px-1 py-0.5 text-[13px] hover:border-hairline focus:border-accent/55 focus-visible:outline-none ${
              source.is_enabled ? "text-text" : "text-text-4"
            }`}
            value={draft ?? source.display_name}
            maxLength={128}
            aria-label={t("market_source_rename", { name: source.display_name })}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={commitName}
            onKeyDown={onNameKeyDown}
          />
          {official && (
            <span className="inline-flex shrink-0 items-center rounded-[5px] border border-accent/35 bg-accent-dim px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-accent-2">
              Official
            </span>
          )}
          {source.index?.homepage && (
            <a
              href={source.index.homepage}
              target="_blank"
              rel="noreferrer"
              className={ICON_BTN_CLS}
              aria-label={t("market_source_homepage", { name: source.display_name })}
            >
              <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          )}
        </div>
        <div className="truncate px-1 font-mono text-[10.5px] text-text-4">{source.address}</div>
        <div className="px-1 text-[11px] text-text-4">
          {refreshing ? t("market_refreshing") : t(`market_status_${source.status}`)} ·{" "}
          {t("market_last_fetched", { time: fetchedAt })}
          {failed && source.last_error && (
            <span className="break-words text-warn"> · {source.last_error}</span>
          )}
        </div>
      </div>
      <button
        type="button"
        className={ICON_BTN_CLS}
        disabled={!source.is_enabled || refreshing}
        aria-label={t("market_source_refresh", { name: source.display_name })}
        onClick={onRefresh}
      >
        <RefreshCw className="h-3.5 w-3.5" aria-hidden />
      </button>
      <button
        type="button"
        className={ICON_BTN_CLS}
        disabled={official}
        title={official ? t("market_source_official_undeletable") : undefined}
        aria-label={t("market_source_delete", { name: source.display_name })}
        onClick={onDelete}
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden />
      </button>
      <span id={switchLabelId} className="sr-only">
        {t("market_source_enable", { name: source.display_name })}
      </span>
      <PillSwitch checked={source.is_enabled} onToggle={onToggle} labelledBy={switchLabelId} />
    </li>
  );
}

function AddSourceForm({ onAdded }: { onAdded: (source: MarketSourceInfo) => void }) {
  const { t } = useTranslation("dashboard");
  const [address, setAddress] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!address.trim() || pending) return;
    setPending(true);
    setError(null);
    try {
      const added = await API.addMarketSource({ address: address.trim() });
      setAddress("");
      onAdded(added);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="space-y-2" onSubmit={(event) => void submit(event)}>
      <div className="flex gap-2">
        <input
          className={INPUT_CLS}
          placeholder={t("market_add_placeholder")}
          aria-label={t("market_add_address_label")}
          value={address}
          disabled={pending}
          onChange={(event) => setAddress(event.target.value)}
        />
        <button
          type="submit"
          className={`${ACCENT_BTN_SM_CLS} shrink-0 whitespace-nowrap`}
          style={ACCENT_BUTTON_STYLE}
          disabled={!address.trim() || pending}
        >
          {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
          {pending ? t("market_add_fetching") : t("market_add_submit")}
        </button>
      </div>
      {error && (
        <p role="alert" className="break-words text-[11.5px] text-danger">
          {error}
        </p>
      )}
      <p className="text-[11px] leading-[1.5] text-text-4">
        <strong className="text-text-3">{t("market_third_party_title")}</strong> —{" "}
        {t("market_third_party_body")}
      </p>
    </form>
  );
}
