// PROTOTYPE — #3091 变体 C「以模型为主轴」的「模型」分区：同步横幅横跨，下面左列表、右条目详情。
import { useState } from "react";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { Button } from "@/components/ui/button";
import { useProto } from "../store";
import { CreateEntryDialog, OrphanDetail } from "./Orphan";
import { EntryDetail } from "./EntryDetail";
import { EntryList } from "./EntryList";
import { SyncBanner, SyncLogSheet } from "./SyncBanner";
import { useParams } from "./shared";

export function ModelsC() {
  const [params, update] = useParams();
  const entries = useProto((s) => s.entries);
  const providers = useProto((s) => s.providers);
  const sync = useProto((s) => s.sync);
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [creating, setCreating] = useState(false);

  const entryId = params.get("entry") ?? (params.get("orphan") ? null : "google/gemini-3.1-pro-preview");
  const orphanKey = params.get("orphan");
  const providerFilter = params.get("p");
  const entry = entries.find((e) => e.id === entryId);
  const orphan = orphanKey ? providers.flatMap((p) => p.models.filter((m) => m.key === orphanKey).map((m) => ({ provider: p, model: m })))[0] : undefined;

  const bannerVisible = sync.status === "offline" || (sync.lastChanges.length > 0 && dismissedVersion !== sync.appliedVersion);
  const select = (id: string) => update({ entry: id, orphan: null });

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {bannerVisible && <SyncBanner onOpenLog={() => setLogOpen(true)} onDismiss={() => setDismissedVersion(sync.appliedVersion)} />}
      <div className="flex min-h-0 min-w-0 flex-1">
        <EntryList
          activeEntry={orphan ? null : (entry?.id ?? null)}
          activeOrphan={orphan?.model.key ?? null}
          providerFilter={providerFilter}
          onSelectEntry={select}
          onSelectOrphan={(key) => update({ orphan: key, entry: null })}
          onClearProvider={() => update({ p: null })}
          onCreate={() => setCreating(true)}
          syncButton={
            !bannerVisible && (
              <Button variant="ghost" size="xs" className="text-muted-foreground tabular-nums" onClick={() => setLogOpen(true)}>
                官方数据 {sync.appliedVersion}
              </Button>
            )
          }
        />
        {orphan ? (
          <OrphanDetail key={orphan.model.key} provider={orphan.provider} model={orphan.model} onLinked={select} />
        ) : entry ? (
          // 换条目或同步状态变化时整栏重建：草稿与「已确认编辑」都属于上一个状态
          <EntryDetail key={`${entry.id}:${entry.syncOn}`} entry={entry} />
        ) : (
          <DetailPane>
            <p className="p-6 text-sm text-muted-foreground">在左侧选择一个模型。</p>
          </DetailPane>
        )}
      </div>
      <SyncLogSheet open={logOpen} onOpenChange={setLogOpen} onJump={select} />
      <CreateEntryDialog open={creating} onOpenChange={setCreating} onCreated={select} />
    </div>
  );
}
