// PROTOTYPE — #3091 变体 B 的「获取模型」：调用供应商的模型列表接口，把还没添加的模型批量加进来。
// 自定义供应商与有列表接口的内置供应商共用。能对应到条目的默认勾选，对应不上的默认不勾。

import { useState } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ENDPOINTS, MEDIA_LABEL, effectiveOf, type CatalogEntry, type EndpointKey, type Provider } from "../model";
import { useProto } from "../store";
import { endpointFor, endpointLabel, matchEntry } from "./bits";

interface Row {
  apiModelName: string;
  entry: CatalogEntry | undefined;
  endpoint: EndpointKey | undefined;
}

export function DiscoverModelsDialog({ provider, onOpenChange }: { provider: Provider; onOpenChange: (v: boolean) => void }) {
  const entries = useProto((s) => s.entries);
  const addProviderModel = useProto((s) => s.addProviderModel);
  const custom = provider.kind === "custom";

  const found = provider.discovered ?? provider.models.map((m) => m.apiModelName);
  // 对话框只在打开时挂载，初始行在挂载时算一次；之后的勾选与端点选择都在本地状态里
  const [rows, setRows] = useState<Row[]>(() => {
    const listed = new Set(provider.models.map((m) => m.apiModelName));
    return found
      .filter((name) => !listed.has(name))
      .map((name) => {
        const entry = matchEntry(entries, name);
        const media = entry ? effectiveOf(entry).mediaType : "text";
        return { apiModelName: name, entry, endpoint: custom ? endpointFor(provider, media, entries) : undefined };
      });
  });
  const [checked, setChecked] = useState(() => new Set(rows.filter((r) => r.entry).map((r) => r.apiModelName)));

  const matched = rows.filter((r) => r.entry);
  const unmatched = rows.filter((r) => !r.entry);
  const alreadyAdded = found.length - rows.length;
  const toggle = (name: string, on: boolean) =>
    setChecked((s) => {
      const next = new Set(s);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });

  const add = () => {
    for (const r of rows.filter((x) => checked.has(x.apiModelName))) {
      addProviderModel(provider.id, {
        key: `${provider.id}:${r.apiModelName}:${Date.now()}`,
        entryId: r.entry?.id ?? null,
        apiModelName: r.apiModelName,
        enabled: true,
        isDefault: false,
        responsibility: "user",
        multiplier: 1,
        ...(r.endpoint ? { endpoint: r.endpoint } : {}),
      });
    }
    onOpenChange(false);
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>从「{provider.displayName}」获取的模型</DialogTitle>
          <DialogDescription>
            {rows.length === 0
              ? `获取到 ${found.length} 个模型，都已在模型列表中。`
              : `获取到 ${found.length} 个模型，其中 ${rows.length} 个尚未添加。勾选要添加的模型。`}
          </DialogDescription>
        </DialogHeader>
        {rows.length > 0 && (
          <DialogBody className="relative flex max-h-[60dvh] flex-col gap-4 overflow-y-auto">
            {matched.length > 0 && (
              <Group title={`有对应的模型条目（${matched.length}）`} hint="能力和价格从条目读取。">
                {matched.map((r) => (
                  <DiscoveredRow key={r.apiModelName} row={r} custom={custom} checked={checked.has(r.apiModelName)} onCheck={toggle} onEndpoint={(ep) => setRows((rs) => rs.map((x) => (x === r ? { ...x, endpoint: ep } : x)))} />
                ))}
              </Group>
            )}
            {unmatched.length > 0 && (
              <Group title={`没有对应的模型条目（${unmatched.length}）`} hint="添加后调用费用记为未定价。可以在模型详情中选择或新建条目。">
                {unmatched.map((r) => (
                  <DiscoveredRow key={r.apiModelName} row={r} custom={custom} checked={checked.has(r.apiModelName)} onCheck={toggle} onEndpoint={(ep) => setRows((rs) => rs.map((x) => (x === r ? { ...x, endpoint: ep } : x)))} />
                ))}
              </Group>
            )}
            {alreadyAdded > 0 && <p className="text-xs text-muted-foreground">另有 {alreadyAdded} 个模型已在模型列表中，未列出。</p>}
          </DialogBody>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {rows.length === 0 ? "关闭" : "取消"}
          </Button>
          {rows.length > 0 && (
            <Button disabled={checked.size === 0} onClick={add}>
              添加 {checked.size} 个模型
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Group({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex flex-col gap-0.5">
        <h3 className="text-sm font-medium">{title}</h3>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">{children}</ul>
    </section>
  );
}

function DiscoveredRow({
  row,
  custom,
  checked,
  onCheck,
  onEndpoint,
}: {
  row: Row;
  custom: boolean;
  checked: boolean;
  onCheck: (name: string, on: boolean) => void;
  onEndpoint: (ep: EndpointKey) => void;
}) {
  const data = row.entry ? effectiveOf(row.entry) : null;
  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <Checkbox checked={checked} onCheckedChange={(v) => onCheck(row.apiModelName, v)} aria-label={`添加 ${row.apiModelName}`} />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-mono text-sm">{row.apiModelName}</span>
        <span className="truncate text-xs text-muted-foreground">{data ? `${data.displayName}（${MEDIA_LABEL[data.mediaType]}）` : "未对应条目"}</span>
      </div>
      {custom && row.endpoint && (
        <Select
          value={row.endpoint}
          items={Object.values(ENDPOINTS).map((e) => ({ value: e.key, label: endpointLabel(e.key) }))}
          onValueChange={(v) => v && onEndpoint(v)}
        >
          <SelectTrigger size="sm" className="w-52" aria-label={`${row.apiModelName} 的调用端点`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.values(ENDPOINTS).map((e) => (
              <SelectItem key={e.key} value={e.key}>
                {endpointLabel(e.key)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </li>
  );
}
