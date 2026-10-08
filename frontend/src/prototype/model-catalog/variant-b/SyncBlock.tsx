// PROTOTYPE — #3091 变体 B 二级栏顶部的同步状态块。只把「影响到你的」变化摆在前面。

import { useState } from "react";
import { Link } from "wouter";
import { RefreshCw } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { effectiveOf } from "../model";
import { referencesOf, useProto } from "../store";
import { providersPath } from "./bits";

const KIND_TEXT = { added: "新收录", updated: "已更新", deprecated: "已下架", skipped: "未应用（你改过）" } as const;
const KIND_TONE = { added: "text-good", updated: "text-subtle-foreground", deprecated: "text-destructive", skipped: "text-warn" } as const;

export function SyncBlock() {
  const sync = useProto((s) => s.sync);
  const entries = useProto((s) => s.entries);
  const providers = useProto((s) => s.providers);
  const check = useProto((s) => s.checkForUpdates);
  const [showUnused, setShowUnused] = useState(false);

  // 「你的模型」只算已配置密钥的供应商下启用的供应商模型
  const withRefs = sync.lastChanges.map((c) => ({
    change: c,
    // 跳过的变化落在你改过的条目上，只要有引用就列出
    refs: referencesOf(providers, c.entryId).filter((r) => c.kind === "skipped" || (r.model.enabled && r.provider.credentialCount > 0)),
  }));
  const used = withRefs.filter((x) => x.refs.length > 0);
  const unused = withRefs.filter((x) => x.refs.length === 0);
  const affectedModels = used.reduce((n, x) => n + x.refs.length, 0);
  const offline = sync.status === "offline";
  const dot = offline ? "bg-muted-foreground" : sync.status === "checking" ? "animate-pulse bg-warn" : "bg-good";
  const nameOf = (id: string) => {
    const e = entries.find((x) => x.id === id);
    return e ? effectiveOf(e).displayName : id;
  };

  return (
    <Popover>
      <PopoverTrigger
        className="group flex w-full flex-col items-start gap-0.5 rounded-md border border-border px-2.5 py-2 text-left outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50"
        aria-label="模型数据同步状态"
      >
        <span className="flex items-center gap-2 text-xs text-subtle-foreground">
          <span aria-hidden className={cn("size-1.5 rounded-full", dot)} />
          {offline ? "离线 · 出厂快照 " + sync.factoryVersion : `官方数据 ${sync.appliedVersion}`}
        </span>
        {!offline && affectedModels > 0 && <span className="text-xs text-foreground">本次更新影响你的 {affectedModels} 个模型</span>}
        {!offline && affectedModels === 0 && <span className="text-xs text-muted-foreground">{sync.lastCheckedAt}检查，与你无关</span>}
      </PopoverTrigger>
      <PopoverContent align="start" side="right" className="w-96 gap-3 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium">模型数据</span>
            <span className="text-xs text-muted-foreground">
              {offline
                ? `无法连接目录数据仓库。目录使用出厂快照（${sync.factoryVersion}）和你的修改，生成不受影响。`
                : `官方数据 ${sync.appliedVersion}，出厂快照 ${sync.factoryVersion}；${sync.lastCheckedAt}检查。有新版本时自动应用，跳过你改过的条目。`}
            </span>
          </div>
          <Button variant="outline" size="sm" disabled={offline || sync.status === "checking"} onClick={check}>
            <RefreshCw aria-hidden className={cn(sync.status === "checking" && "animate-spin")} />
            检查更新
          </Button>
        </div>
        {!offline && (
          <>
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">上次更新影响你的模型</span>
              {used.length === 0 && <p className="text-xs text-muted-foreground">没有。</p>}
              <ul className="flex flex-col gap-1.5">
                {used.map(({ change, refs }) => (
                  <li key={change.entryId} className="rounded-md bg-muted/40 px-2.5 py-1.5">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate">{nameOf(change.entryId)}</span>
                      <span className={cn("shrink-0 text-xs", KIND_TONE[change.kind])}>{KIND_TEXT[change.kind]}</span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1">
                      {change.summary !== KIND_TEXT[change.kind] && <span className="mr-1 text-xs text-muted-foreground">{change.summary}</span>}
                      {refs.map((r) => (
                        <Link
                          key={r.model.key}
                          href={providersPath(r.provider, r.model.key)}
                          className="rounded border border-border px-1.5 py-0.5 text-xs text-subtle-foreground hover:border-primary/50 hover:text-foreground"
                        >
                          {r.provider.displayName}
                        </Link>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
            {unused.length > 0 && (
              <div className="flex flex-col gap-1">
                <button type="button" className="self-start text-xs text-muted-foreground hover:text-foreground" onClick={() => setShowUnused((v) => !v)}>
                  另有 {unused.length} 项与你启用的模型无关{showUnused ? "，收起" : "，展开"}
                </button>
                {showUnused && (
                  <ul className="flex flex-col gap-0.5">
                    {unused.map(({ change }) => (
                      <li key={change.entryId} className="flex gap-2 text-xs text-muted-foreground">
                        <span className="min-w-0 flex-1 truncate">{nameOf(change.entryId)}</span>
                        <span>{change.summary}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
