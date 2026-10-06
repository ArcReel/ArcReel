import { useCallback, useId, useRef, useState, type FormEvent } from "react";
import { ArrowDown, ArrowUp, ExternalLink, Loader2, MoreHorizontal, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { SortableHandle, SortableItem, SortableList, useSortableMove } from "@/components/shared/sortable/SortableList";
import { TruncatedText } from "@/components/shared/TruncatedText";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useAppStore } from "@/stores/app-store";
import type { MarketSourceInfo } from "@/types";
import { errMsg } from "@/utils/async";
import { formatRelativeTime } from "@/utils/date-format";
import { SourceStatusDot } from "./market-source-status";

type SourcePatch = { display_name?: string; is_enabled?: boolean };

/** 只改写一行里给定的字段；同一行的并发修改各自只动自己提交的字段，响应先后不会互相覆盖。 */
function patchSource(current: MarketSourceInfo[], id: number, fields: Partial<MarketSourceInfo>): MarketSourceInfo[] {
  return current.map((source) => (source.id === id ? { ...source, ...fields } : source));
}

function pickPatchFields(source: MarketSourceInfo, patch: SourcePatch): SourcePatch {
  return Object.fromEntries((Object.keys(patch) as (keyof SourcePatch)[]).map((key) => [key, source[key]]));
}

/** 按 `ordered` 的顺序与 position 重排当前行，行内其他字段保留本地值；不在其中的行排在最后。 */
function applyOrder(current: MarketSourceInfo[], ordered: MarketSourceInfo[]): MarketSourceInfo[] {
  const slots = new Map(ordered.map((source, index) => [source.id, { index, position: source.position }]));
  const slotIndex = (source: MarketSourceInfo) => slots.get(source.id)?.index ?? ordered.length;
  return current
    .map((source) => {
      const slot = slots.get(source.id);
      return slot ? { ...source, position: slot.position } : source;
    })
    .sort((a, b) => slotIndex(a) - slotIndex(b));
}

interface SequencedMutation<T, V> {
  /** 发起时本地的值，作为这一串修改开始前最近一次确认的值。 */
  before: V;
  send: () => Promise<T>;
  confirmedValue: (result: T) => V;
  applied: (result: T) => void;
  rolledBack: (confirmed: V) => void;
}

interface MutationLane {
  tail: Promise<void>;
  generation: number;
  confirmed: unknown;
}

/**
 * 同一 key 的修改按发起顺序串行发送，服务端按用户操作顺序落库；响应与失败回滚只在该 key 没有
 * 更新的修改时写回本地，回滚恢复到最近一次确认的值。失败一律抛出，由调用方提示。
 */
function useSequencedMutations() {
  const lanes = useRef(new Map<string, MutationLane>());
  return useCallback(async <T, V>(key: string, mutation: SequencedMutation<T, V>) => {
    const pending = lanes.current.get(key);
    const lane = pending ?? { tail: Promise.resolve(), generation: 0, confirmed: mutation.before };
    lanes.current.set(key, lane);
    const generation = ++lane.generation;
    const run = pending ? lane.tail.then(mutation.send) : mutation.send();
    lane.tail = run.then(
      () => undefined,
      () => undefined,
    );
    const latest = () => lane.generation === generation;
    try {
      const result = await run;
      lane.confirmed = mutation.confirmedValue(result);
      if (latest()) mutation.applied(result);
    } catch (err) {
      if (latest()) mutation.rolledBack(lane.confirmed as V);
      throw err;
    } finally {
      if (latest()) lanes.current.delete(key);
    }
  }, []);
}

interface MarketSourceListProps {
  sources: MarketSourceInfo[];
  onSourcesChange: (update: (current: MarketSourceInfo[]) => MarketSourceInfo[]) => void;
  refreshingIds: ReadonlySet<number>;
  refreshingAll: boolean;
  onRefresh: (id: number) => void;
  onRefreshAll: () => void;
}

/**
 * 市场「设置」里的市场源：排序（拖动把手、键盘或菜单的上移下移）、启停、改名、单源刷新、删除与添加，都即时生效。
 * 失败时回滚本地状态并弹出提示；成功只以列表变化为反馈。删除不可撤销，先用 AlertDialog 确认。
 */
export function MarketSourceList({
  sources,
  onSourcesChange,
  refreshingIds,
  refreshingAll,
  onRefresh,
  onRefreshAll,
}: MarketSourceListProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const titleId = useId();
  const mutate = useSequencedMutations();
  const [renaming, setRenaming] = useState<MarketSourceInfo | null>(null);
  const [deleting, setDeleting] = useState<MarketSourceInfo | null>(null);

  const failToast = (err: unknown) => pushToast(t("market_action_failed", { message: errMsg(err) }), "error");

  const commitOrder = async (ids: number[]) => {
    const byId = new Map(sources.map((source) => [source.id, source]));
    const next = ids.flatMap((id) => byId.get(id) ?? []);
    onSourcesChange((current) => applyOrder(current, next));
    try {
      await mutate("order", {
        before: sources,
        send: () => API.reorderMarketSources(ids),
        confirmedValue: ({ sources: saved }) => saved,
        applied: ({ sources: saved }) => onSourcesChange((current) => applyOrder(current, saved)),
        rolledBack: (confirmed) => onSourcesChange((current) => applyOrder(current, confirmed)),
      });
    } catch (err) {
      failToast(err);
    }
  };

  const update = async (id: number, patch: SourcePatch) => {
    const previous = sources.find((source) => source.id === id);
    if (!previous) return;
    onSourcesChange((current) => patchSource(current, id, patch));
    try {
      await mutate(`${id}:${Object.keys(patch).join(",")}`, {
        before: pickPatchFields(previous, patch),
        send: () => API.updateMarketSource(id, patch),
        confirmedValue: (saved) => pickPatchFields(saved, patch),
        applied: (saved) =>
          onSourcesChange((current) =>
            patchSource(current, id, { ...pickPatchFields(saved, patch), updated_at: saved.updated_at }),
          ),
        rolledBack: (confirmed) => onSourcesChange((current) => patchSource(current, id, confirmed)),
      });
    } catch (err) {
      failToast(err);
    }
  };

  const names = new Map(sources.map((source) => [source.id, source.display_name]));

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id={titleId} className="text-sm font-medium">
            {t("market_sources_title")}
          </h3>
          <p className="text-sm text-muted-foreground">{t("market_sources_desc")}</p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          disabled={refreshingAll || !sources.some((source) => source.is_enabled)}
          onClick={onRefreshAll}
        >
          {refreshingAll ? (
            <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
          ) : (
            <RefreshCw data-icon="inline-start" aria-hidden />
          )}
          {t("market_refresh_all")}
        </Button>
      </div>

      <div className="flex flex-col rounded-lg border border-border bg-card">
        <SortableList
          ids={sources.map((source) => source.id)}
          getName={(id) => names.get(id) ?? ""}
          onMove={({ ids }) => void commitOrder(ids)}
        >
          <ul aria-labelledby={titleId} className="flex flex-col divide-y divide-border">
            {sources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                refreshing={refreshingIds.has(source.id)}
                onToggle={() => void update(source.id, { is_enabled: !source.is_enabled })}
                onRefresh={() => onRefresh(source.id)}
                onRename={() => setRenaming(source)}
                onDelete={() => setDeleting(source)}
              />
            ))}
          </ul>
        </SortableList>
        <div className="border-t border-border p-3">
          <AddSourceForm onAdded={(added) => onSourcesChange((current) => [...current, added])} />
        </div>
      </div>

      <RenameSourceDialog
        source={renaming}
        onClose={() => setRenaming(null)}
        onRename={(id, name) => void update(id, { display_name: name })}
      />
      <DeleteSourceDialog
        source={deleting}
        onClose={() => setDeleting(null)}
        onDeleted={(id) => onSourcesChange((current) => current.filter((source) => source.id !== id))}
      />
    </section>
  );
}

interface SourceRowProps {
  source: MarketSourceInfo;
  refreshing: boolean;
  onToggle: () => void;
  onRefresh: () => void;
  onRename: () => void;
  onDelete: () => void;
}

function SourceRow({ source, refreshing, onToggle, onRefresh, onRename, onDelete }: SourceRowProps) {
  const { t, i18n } = useTranslation("dashboard");
  const move = useSortableMove(source.id);
  const official = source.kind === "official";
  const homepage = source.index?.homepage;
  const failed = source.status !== "ok" && source.status !== "never_fetched";
  const fetchedAt = formatRelativeTime(source.fetched_at, i18n.language) ?? t("market_never_fetched_time");
  const name = source.display_name;

  return (
    <SortableItem
      id={source.id}
      className="flex min-w-0 items-center gap-2 bg-card py-2.5 pr-3 pl-1.5 data-dragging:shadow-overlay"
    >
      <SortableHandle label={t("market_source_reorder", { name })} />
      <SourceStatusDot source={source} refreshing={refreshing} />
      <div className="ml-1 flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <TruncatedText text={name} className="text-sm font-medium" />
          {official && <Badge variant="secondary">{t("market_source_official")}</Badge>}
        </div>
        <TruncatedText text={source.address} className="font-mono text-xs text-muted-foreground" />
        <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
          {refreshing ? (
            <span>{t("market_refreshing")}</span>
          ) : !source.is_enabled ? (
            <span>{t("market_source_status_disabled")}</span>
          ) : (
            <>
              <span className={failed ? "text-warn" : undefined}>
                {source.status === "ok"
                  ? t("market_source_entries", { count: source.entry_count })
                  : t(`market_status_${source.status}`)}
              </span>
              <span>{t("market_last_fetched", { time: fetchedAt })}</span>
            </>
          )}
        </p>
        {failed && source.is_enabled && source.last_error && (
          <p className="text-xs break-words text-warn">{source.last_error}</p>
        )}
      </div>
      <Switch
        checked={source.is_enabled}
        onCheckedChange={onToggle}
        aria-label={t("market_source_enable", { name })}
      />
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button variant="ghost" size="icon-sm" aria-label={t("market_source_menu", { name })} />}
        >
          <MoreHorizontal aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuItem disabled={!move.canMoveBy(-1)} onClick={() => move.moveBy(-1)}>
            <ArrowUp aria-hidden />
            {t("market_source_move_up")}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!move.canMoveBy(1)} onClick={() => move.moveBy(1)}>
            <ArrowDown aria-hidden />
            {t("market_source_move_down")}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!source.is_enabled || refreshing} onClick={onRefresh}>
            <RefreshCw aria-hidden />
            {t("market_source_refresh_action")}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={onRename}>
            <Pencil aria-hidden />
            {t("market_source_rename_action")}
          </DropdownMenuItem>
          {homepage && (
            <DropdownMenuItem onClick={() => window.open(homepage, "_blank", "noopener,noreferrer")}>
              <ExternalLink aria-hidden />
              {t("market_source_homepage_action")}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" disabled={official} onClick={onDelete}>
            <Trash2 aria-hidden />
            {t("market_source_delete_action")}
          </DropdownMenuItem>
          {official && (
            <p className="px-1.5 py-1 text-xs text-muted-foreground">{t("market_source_official_undeletable")}</p>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </SortableItem>
  );
}

function RenameSourceDialog({
  source,
  onClose,
  onRename,
}: {
  source: MarketSourceInfo | null;
  onClose: () => void;
  onRename: (id: number, name: string) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const formId = useId();
  const inputId = useId();
  // 对话框关闭动画期间 source 已是 null，保留上一次的内容避免闪成空白。每次打开（含再次打开同一个源）都从源的当前名称开始。
  const [prevSource, setPrevSource] = useState<MarketSourceInfo | null>(null);
  const [shown, setShown] = useState<MarketSourceInfo | null>(null);
  const [name, setName] = useState("");
  if (source !== prevSource) {
    setPrevSource(source);
    if (source) {
      setShown(source);
      setName(source.display_name);
    }
  }
  const trimmed = name.trim();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!shown || !trimmed) return;
    if (trimmed !== shown.display_name) onRename(shown.id, trimmed);
    onClose();
  };

  return (
    <Dialog
      open={source !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{t("market_source_rename_title")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <form id={formId} className="flex flex-col gap-2" onSubmit={submit}>
            <Label htmlFor={inputId}>{t("market_source_name_label")}</Label>
            <Input id={inputId} value={name} maxLength={128} onChange={(event) => setName(event.target.value)} />
          </form>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" form={formId} disabled={!trimmed}>
            {t("common:save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteSourceDialog({
  source,
  onClose,
  onDeleted,
}: {
  source: MarketSourceInfo | null;
  onClose: () => void;
  onDeleted: (id: number) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  // 关闭动画期间保留上一次的源；每次打开都清掉上一次的错误。
  const [prevSource, setPrevSource] = useState<MarketSourceInfo | null>(null);
  const [shown, setShown] = useState<MarketSourceInfo | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (source !== prevSource) {
    setPrevSource(source);
    if (source) {
      setShown(source);
      setError(null);
    }
  }

  const handleDelete = async () => {
    if (!shown) return;
    setSubmitting(true);
    setError(null);
    try {
      await API.deleteMarketSource(shown.id);
      onDeleted(shown.id);
      onClose();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AlertDialog
      open={source !== null}
      onOpenChange={(next) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!next && !submitting) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("market_source_delete_title", { name: shown?.display_name ?? "" })}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("market_source_delete_title", { name: shown?.display_name ?? "" })}>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>{t("market_source_delete_desc")}</AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={submitting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={submitting} onClick={() => void handleDelete()}>
            {submitting && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("market_source_delete_action")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function AddSourceForm({ onAdded }: { onAdded: (source: MarketSourceInfo) => void }) {
  const { t } = useTranslation("dashboard");
  const noteId = useId();
  const errorId = useId();
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
    <form className="flex flex-col gap-2" onSubmit={(event) => void submit(event)}>
      <div className="flex gap-2">
        <Input
          aria-label={t("market_add_address_label")}
          aria-describedby={error ? `${errorId} ${noteId}` : noteId}
          aria-invalid={error ? true : undefined}
          placeholder={t("market_add_placeholder")}
          value={address}
          disabled={pending}
          onChange={(event) => setAddress(event.target.value)}
        />
        <Button type="submit" variant="outline" className="shrink-0" disabled={!address.trim() || pending}>
          {pending && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
          {pending ? t("market_add_fetching") : t("market_add_submit")}
        </Button>
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-sm break-words text-destructive">
          {error}
        </p>
      )}
      <p id={noteId} className="text-xs text-muted-foreground">
        {t("market_third_party_body")}
      </p>
    </form>
  );
}
