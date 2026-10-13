// PROTOTYPE — #3091 变体 B「条目嵌在供应商里」。
// 侧栏不新增分区：一切从「供应商」进入。供应商模型列表点开右侧 Sheet，上段是本供应商的设置，下段是共用的模型条目；
// 浏览整个目录只经「添加模型」的 Command 面板；有模型列表接口的供应商另有「获取模型」。
// 官方同步全自动、不打扰，界面上不设全局同步状态，只在用户维护的条目上就地标出官方值。

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { Download, Loader2, Plus } from "lucide-react";
import { cn } from "cn";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SecondaryRail, type SecondaryRailGroup } from "@/components/shared/master-detail/SecondaryRail";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  ENDPOINTS,
  MEDIA_LABEL,
  MEDIA_TYPES,
  effectiveOf,
  priceSummary,
  type CatalogEntry,
  type MediaType,
  type Provider,
  type ProviderModel,
} from "../model";
import { useProto } from "../store";
import { AddModelDialog } from "./AddModel";
import { Chip, FlagBadge, flagsOf, providersPath, type ModelFlag } from "./bits";
import { DiscoverModelsDialog } from "./DiscoverModels";
import { ModelSheet } from "./ModelSheet";

function Glyph({ name }: { name: string }) {
  return (
    <span className="inline-flex size-4 items-center justify-center rounded-sm border border-border bg-muted text-xs leading-none text-subtle-foreground">
      {Array.from(name)[0] ?? "?"}
    </span>
  );
}

export function ProvidersView() {
  const providers = useProto((s) => s.providers);
  const search = useSearch();
  const [, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const selectedId = params.get("custom") ?? params.get("provider");
  const pmKey = params.get("pm");
  const provider = providers.find((p) => p.id === selectedId) ?? null;

  useEffect(() => {
    if (!provider && providers[0]) navigate(providersPath(providers[0]), { replace: true });
  }, [provider, providers, navigate]);

  const groups = useMemo<SecondaryRailGroup[]>(
    () => [
      {
        id: "preset",
        label: "预置",
        items: providers
          .filter((p) => p.kind === "builtin")
          .map((p) => ({
            id: p.id,
            label: p.displayName,
            description: p.credentialCount > 0 ? `已启用 ${p.models.filter((m) => m.enabled).length} 个模型` : "未配置密钥",
            icon: <ProviderIcon providerId={p.id} className="size-4" />,
            href: providersPath(p),
          })),
      },
      {
        id: "custom",
        label: "自定义",
        items: providers
          .filter((p) => p.kind === "custom")
          .map((p) => ({
            id: p.id,
            label: p.displayName,
            description: `${p.models.length} 个模型`,
            icon: <Glyph name={p.displayName} />,
            href: providersPath(p),
          })),
        action: { id: "custom:new", label: "添加自定义供应商", icon: <Plus className="size-4" />, href: "#" },
      },
    ],
    [providers],
  );

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail label="供应商列表" groups={groups} activeId={provider?.id ?? null} replace />
      {provider ? (
        <ProviderPane key={provider.id} provider={provider} pmKey={pmKey} />
      ) : (
        <DetailPane>
          <p className="p-6 text-sm text-muted-foreground">选择一个供应商</p>
        </DetailPane>
      )}
    </div>
  );
}

type Filter = "all" | "enabled";
const FILTERS: { id: Filter; label: string; test: (m: ProviderModel, flags: ModelFlag[]) => boolean }[] = [
  { id: "all", label: "全部", test: () => true },
  { id: "enabled", label: "已启用", test: (m) => m.enabled },
];

function ProviderPane({ provider, pmKey }: { provider: Provider; pmKey: string | null }) {
  const entries = useProto((s) => s.entries);
  const updateProvider = useProto((s) => s.updateProvider);
  const updateProviderModel = useProto((s) => s.updateProviderModel);
  const [, navigate] = useLocation();
  const [filter, setFilter] = useState<Filter>("all");
  const [adding, setAdding] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [discovered, setDiscovered] = useState(false);
  const fetchModels = () => {
    setFetching(true);
    window.setTimeout(() => {
      setFetching(false);
      setDiscovered(true);
    }, 700);
  };

  const entryOf = useCallback((id: string | null) => entries.find((e) => e.id === id), [entries]);
  const rows = provider.models.map((m) => ({ model: m, entry: entryOf(m.entryId), flags: flagsOf(m, entryOf(m.entryId)) }));
  const visible = rows.filter((r) => FILTERS.find((f) => f.id === filter)!.test(r.model, r.flags));
  const mediaOf = (r: { model: ProviderModel; entry?: CatalogEntry }): MediaType =>
    r.entry ? effectiveOf(r.entry).mediaType : r.model.endpoint ? ENDPOINTS[r.model.endpoint].mediaType : "text";

  const source = useMemo(() => ({ baseUrl: provider.baseUrl ?? "", multiplier: provider.multiplier }), [provider.baseUrl, provider.multiplier]);
  const save = useCallback(
    async (v: typeof source) => updateProvider(provider.id, { multiplier: v.multiplier, ...(provider.kind === "custom" ? { baseUrl: v.baseUrl } : {}) }),
    [provider.id, provider.kind, updateProvider],
  );
  const unit = useEditUnit({ source, save });
  const openModel = provider.models.find((m) => m.key === pmKey);

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-3">
          {provider.kind === "builtin" ? <ProviderIcon providerId={provider.id} className="mt-0.5 size-7 shrink-0" /> : <Glyph name={provider.displayName} />}
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h2 className="text-lg font-medium">{provider.displayName}</h2>
          </div>
        </div>
      }
      footer={<SaveBar unit={unit} className="max-w-178" />}
    >
      <div className="flex max-w-190 flex-col gap-6 px-6 py-6">
        <section className="flex flex-wrap items-end gap-4">
          {provider.kind === "custom" && (
            <label className="flex min-w-80 flex-1 flex-col gap-1.5 text-sm font-medium">
              接口地址
              <Input className="font-mono font-normal" value={unit.value.baseUrl} onChange={(e) => unit.setValue((v) => ({ ...v, baseUrl: e.target.value }))} />
            </label>
          )}
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            倍率
            <div className="flex items-center gap-2">
              <Input
                type="number"
                step="0.05"
                min={0}
                className="w-24 font-normal tabular-nums"
                value={unit.value.multiplier}
                onChange={(e) => unit.setValue((v) => ({ ...v, multiplier: Number(e.target.value) }))}
              />
              <span className="text-xs font-normal text-muted-foreground">
                {provider.id === "ark-agent-plan" ? "订阅制套餐，默认为 0，不计算费用。" : "费用按官方价格乘以倍率计算，例如 0.8 表示八折。"}
              </span>
            </div>
          </label>
        </section>

        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base font-medium">模型</h3>
            <div className="flex items-center gap-2">
              {provider.discoverable && (
                <Button size="sm" variant="outline" disabled={fetching} onClick={fetchModels}>
                  {fetching ? <Loader2 aria-hidden className="animate-spin" /> : <Download aria-hidden />}
                  {fetching ? "获取中……" : "获取模型"}
                </Button>
              )}
              <Button size="sm" onClick={() => setAdding(true)}>
                <Plus aria-hidden />
                添加模型
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <Chip key={f.id} active={filter === f.id} onClick={() => setFilter(f.id)} count={rows.filter((r) => f.test(r.model, r.flags)).length}>
                {f.label}
              </Chip>
            ))}
          </div>

          {visible.length === 0 && <p className="py-6 text-sm text-muted-foreground">没有符合条件的模型。</p>}
          {MEDIA_TYPES.map((media) => {
            const group = visible.filter((r) => mediaOf(r) === media);
            if (!group.length) return null;
            return (
              <div key={media} className="flex flex-col">
                <h4 className="px-1 pb-1 text-xs text-muted-foreground">
                  {MEDIA_LABEL[media]}（{group.length}）
                </h4>
                <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">
                  {group.map(({ model, entry, flags }) => {
                    const data = entry ? effectiveOf(entry) : null;
                    return (
                      <li key={model.key} className={cn("flex items-center gap-3 px-3 py-2 hover:bg-muted/40", !model.enabled && "opacity-60")}>
                        <Switch
                          size="sm"
                          checked={model.enabled}
                          aria-label={`启用 ${data?.displayName ?? model.apiModelName}`}
                          onCheckedChange={(v) => updateProviderModel(provider.id, model.key, { enabled: v, ...(v ? {} : { isDefault: false }) })}
                        />
                        <button
                          type="button"
                          className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                          onClick={() => navigate(providersPath(provider, model.key), { replace: true })}
                        >
                          <span className="flex max-w-full flex-wrap items-center gap-1.5">
                            <span className="truncate text-sm">{data?.displayName ?? model.apiModelName}</span>
                            {model.isDefault && <span className="rounded-full bg-primary/15 px-1.5 text-xs text-primary">默认</span>}
                            {flags.map((f) => (
                              <FlagBadge key={f} flag={f} />
                            ))}
                          </span>
                          <span className="flex max-w-full items-center gap-3 truncate text-xs text-muted-foreground">
                            <span className="font-mono">{model.apiModelName}</span>
                            {data && data.price.meters.length > 0 && <span>{priceSummary(data.price)}</span>}
                            {model.endpoint && <span>{ENDPOINTS[model.endpoint].label}</span>}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </section>
      </div>

      {discovered && <DiscoverModelsDialog provider={provider} onOpenChange={setDiscovered} />}
      <AddModelDialog provider={provider} open={adding} onOpenChange={setAdding} onAdded={(key) => navigate(providersPath(provider, key), { replace: true })} />
      {openModel && (
        <ModelSheet key={openModel.key} provider={provider} model={openModel} onClose={() => navigate(providersPath(provider), { replace: true })} />
      )}
    </DetailPane>
  );
}

export function ModelsView() {
  return <div className="p-6 text-sm text-muted-foreground">变体 B 不设独立的模型分区。</div>;
}
