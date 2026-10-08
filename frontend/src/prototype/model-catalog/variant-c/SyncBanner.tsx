// PROTOTYPE — #3091 变体 C：同步横幅与更新日志抽屉。
// 横幅只在有值得注意的事时出现（刚应用了一批变化、或离线）；看过后收为分区页头里的一个小按钮。
import { useState } from "react";
import { CloudOff, Loader2, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { effectiveOf } from "../model";
import { useProto, type SyncChange } from "../store";

const KIND_TITLE: Record<SyncChange["kind"], string> = {
  added: "新收录",
  updated: "已更新",
  deprecated: "官方已下架",
  skipped: "未应用：你改过这些条目",
};
const KIND_ORDER: SyncChange["kind"][] = ["skipped", "added", "updated", "deprecated"];

export function useSyncSummary() {
  const sync = useProto((s) => s.sync);
  const applied = sync.lastChanges.filter((c) => c.kind !== "skipped").length;
  const skipped = sync.lastChanges.filter((c) => c.kind === "skipped").length;
  return { sync, applied, skipped };
}

export function SyncBanner({ onOpenLog, onDismiss }: { onOpenLog: () => void; onDismiss: () => void }) {
  const { sync, applied, skipped } = useSyncSummary();
  if (sync.status === "offline") {
    return (
      <div role="status" className="flex shrink-0 items-center gap-3 border-b border-border bg-muted/40 px-6 py-2.5 text-sm">
        <CloudOff aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <p className="min-w-0 flex-1 text-muted-foreground">
          无法连接目录数据仓库，模型目录暂用本地数据（官方数据 {sync.appliedVersion}）。不影响生成，联网后会自动更新。
        </p>
        <Button variant="ghost" size="sm" onClick={onOpenLog}>
          详情
        </Button>
      </div>
    );
  }
  return (
    <div role="status" className="flex shrink-0 items-center gap-3 border-b border-border bg-primary/8 px-6 py-2.5 text-sm">
      <RefreshCw aria-hidden className="size-4 shrink-0 text-primary" />
      <p className="min-w-0 flex-1">
        官方数据已更新到 {sync.appliedVersion}：{applied} 项变化
        {skipped > 0 && <span className="text-warn">，{skipped} 项因你改过而未应用</span>}
      </p>
      <Button variant="outline" size="sm" onClick={onOpenLog}>
        查看
      </Button>
      <Button variant="ghost" size="icon-sm" aria-label="收起提示" onClick={onDismiss}>
        <X aria-hidden />
      </Button>
    </div>
  );
}

export function SyncLogSheet({ open, onOpenChange, onJump }: { open: boolean; onOpenChange: (v: boolean) => void; onJump: (entryId: string) => void }) {
  const { sync } = useSyncSummary();
  const entries = useProto((s) => s.entries);
  const check = useProto((s) => s.checkForUpdates);
  const [, force] = useState(0);
  const name = (id: string) => {
    const e = entries.find((x) => x.id === id);
    return e ? effectiveOf(e).displayName : id;
  };
  const offline = sync.status === "offline";
  const checking = sync.status === "checking";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="data-[side=right]:w-[min(440px,calc(100vw-3rem))]">
        <SheetHeader>
          <SheetTitle>官方数据</SheetTitle>
          <SheetDescription>模型目录的价格与元信息来自目录数据仓库，有新版本就自动整批应用；关了官方同步的条目会被跳过。</SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-6">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">当前版本</dt>
            <dd className="tabular-nums">{sync.appliedVersion}</dd>
            <dt className="text-muted-foreground">出厂快照</dt>
            <dd className="tabular-nums">{sync.factoryVersion}（随 ArcReel 0.33.0 发布）</dd>
            <dt className="text-muted-foreground">上次检查</dt>
            <dd>{offline ? "无法连接，稍后自动重试" : checking ? "正在检查…" : sync.lastCheckedAt}</dd>
          </dl>

          <div className="flex flex-col gap-5">
            <h3 className="text-sm font-medium">最近一次更新带来的变化</h3>
            {sync.lastChanges.length === 0 && <p className="text-sm text-muted-foreground">没有变化。</p>}
            {KIND_ORDER.map((kind) => {
              const items = sync.lastChanges.filter((c) => c.kind === kind);
              if (!items.length) return null;
              return (
                <section key={kind} className="flex flex-col gap-1.5">
                  <h4 className={kind === "skipped" ? "text-sm text-warn" : "text-sm text-subtle-foreground"}>
                    {KIND_TITLE[kind]} · {items.length}
                  </h4>
                  <ul className="flex flex-col">
                    {items.map((c) => (
                      <li key={`${c.kind}:${c.entryId}`}>
                        <button
                          type="button"
                          onClick={() => {
                            onJump(c.entryId);
                            onOpenChange(false);
                          }}
                          className="flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-muted"
                        >
                          <span className="text-sm">{name(c.entryId)}</span>
                          <span className="text-xs text-muted-foreground">{c.summary}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        </SheetBody>
        <SheetFooter>
          <Button
            variant="outline"
            disabled={offline || checking}
            onClick={() => {
              check();
              force((n) => n + 1);
            }}
          >
            {checking ? <Loader2 aria-hidden className="animate-spin" /> : <RefreshCw aria-hidden />}
            检查更新
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
