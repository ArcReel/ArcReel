// PROTOTYPE — #3091 变体 A：「供应商」分区。供应商详情只管渠道事实，模型事实与价格链接到「模型目录」。
import { Fragment, useCallback, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Download, ExternalLink, KeyRound, Plus, Trash2 } from "lucide-react";
import { Link } from "wouter";
import { cn } from "cn";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SecondaryRail, type SecondaryRailGroup } from "@/components/shared/master-detail/SecondaryRail";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  ENDPOINTS,
  MEDIA_LABEL,
  effectiveMultiplier,
  effectiveOf,
  type CatalogEntry,
  type EndpointKey,
  type Provider,
  type ProviderModel,
} from "../model";
import { useProto } from "../store";
import { CapabilityOverrideEditor, EntryPicker, PathOverrideTable } from "./ChannelEditors";
import { FieldLabel, Section, SimpleSelect, modelsPath, providerPath, scaledSummary, useParam } from "./shared";

function CustomGlyph({ name }: { name: string }) {
  return (
    <span className="inline-flex size-4 items-center justify-center rounded-sm border border-border bg-muted text-xs leading-none text-subtle-foreground">
      {Array.from(name)[0] ?? "?"}
    </span>
  );
}

const entryOf = (entries: CatalogEntry[], id: string | null) => entries.find((e) => e.id === id);

export function ProvidersView() {
  const providers = useProto((s) => s.providers);
  const pv = useParam("pv") ?? providers[0]?.id;
  const selected = providers.find((p) => p.id === pv);

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
            description: p.credentialCount ? `${p.credentialCount} 个密钥 · ${p.models.filter((m) => m.enabled).length} 个模型启用` : "未配置",
            icon: <ProviderIcon providerId={p.id} className="size-4" />,
            href: providerPath(p.id),
          })),
      },
      {
        id: "custom",
        label: "自定义",
        items: providers
          .filter((p) => p.kind === "custom")
          .map((p) => ({ id: p.id, label: p.displayName, description: `${p.models.length} 个模型`, icon: <CustomGlyph name={p.displayName} />, href: providerPath(p.id) })),
      },
    ],
    [providers],
  );

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail label="供应商列表" groups={groups} activeId={selected?.id ?? null} replace />
      {selected?.kind === "builtin" && <BuiltinDetail key={selected.id} provider={selected} />}
      {selected?.kind === "custom" && <CustomDetail key={selected.id} provider={selected} />}
    </div>
  );
}

// ---------- 内置供应商 ----------

function BuiltinDetail({ provider }: { provider: Provider }) {
  const entries = useProto((s) => s.entries);
  const update = useProto((s) => s.updateProviderModel);
  const setDefault = useProto((s) => s.setDefault);
  const remove = useProto((s) => s.removeProviderModel);
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const isPlan = provider.multiplier === 0;

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-3">
          <ProviderIcon providerId={provider.id} className="mt-0.5 size-7 shrink-0" />
          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-medium">{provider.displayName}</h2>
              <Badge variant={provider.credentialCount ? "secondary" : "outline"}>{provider.credentialCount ? "已就绪" : "未配置"}</Badge>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {provider.mediaTypes.map((m) => (
                <Badge key={m} variant="outline">
                  {MEDIA_LABEL[m]}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      }
    >
      <div className="flex max-w-[64rem] flex-col gap-8 px-6 py-6">
        <Section title="密钥" hint="（原型里简化为一行，真实界面沿用现有密钥区。）">
          <div className="flex items-center gap-3 rounded-lg border border-border px-4 py-3 text-sm">
            <KeyRound className="size-4 text-muted-foreground" />
            <span className="flex-1">{provider.credentialCount ? `${provider.credentialCount} 个密钥，当前使用「默认」` : "还没有密钥"}</span>
            <Button variant="outline" size="sm" disabled>
              管理密钥
            </Button>
          </div>
        </Section>

        <Section
          title="供应商模型"
          hint={
            isPlan ? (
              <>这个供应商按订阅计费，倍率为 0：调用记 0 元，不查价。</>
            ) : (
              <>官方收录的模型随官方数据更新，只能启用、停用和设为默认；价格与能力在「模型目录」里看。启用与默认即时生效。</>
            )
          }
          action={
            <Button variant="outline" size="sm" disabled={!provider.discoverable} onClick={() => setDiscoverOpen(true)} title={provider.discoverable ? undefined : "这个供应商没有模型列表接口，只能按 id 手动添加"}>
              <Download data-icon="inline-start" />
              获取模型
            </Button>
          }
        >
          {!provider.discoverable && <p className="-mt-1 text-xs text-subtle-foreground">这个供应商没有模型列表接口，只能按 id 手动添加。</p>}
          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full table-fixed text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="w-14 px-3 py-2 text-left font-normal">启用</th>
                  <th className="px-2 py-2 text-left font-normal">模型</th>
                  <th className="hidden w-56 px-2 py-2 text-left font-normal @6xl/page:table-cell">API 模型名</th>
                  <th className="w-12 px-2 py-2 text-left font-normal">类型</th>
                  <th className="w-56 px-2 py-2 text-left font-normal">参考价</th>
                  <th className="w-14 px-3 py-2 text-left font-normal">默认</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {provider.models.map((m) => {
                  const e = entryOf(entries, m.entryId);
                  const d = e ? effectiveOf(e) : null;
                  const userOwned = m.responsibility === "user";
                  return (
                    <Fragment key={m.key}>
                      <tr className={cn(!m.enabled && "text-muted-foreground")}>
                        <td className="px-3 py-2">
                          <Switch size="sm" checked={m.enabled} onCheckedChange={(on) => update(provider.id, m.key, { enabled: on, ...(on ? {} : { isDefault: false }) })} aria-label={`启用 ${m.apiModelName}`} />
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex min-w-0 items-center gap-2">
                            {userOwned && (
                              <button type="button" aria-label="展开编辑" className="text-muted-foreground hover:text-foreground" onClick={() => setExpanded(expanded === m.key ? null : m.key)}>
                                {expanded === m.key ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                              </button>
                            )}
                            {e && d ? (
                              <Link href={modelsPath(e.id)} className="truncate hover:underline">
                                {d.displayName}
                              </Link>
                            ) : (
                              <span className="truncate text-warn">未对应条目</span>
                            )}
                            <span className="shrink-0 text-xs text-subtle-foreground">{userOwned ? "用户添加" : "官方收录"}</span>
                            {d?.deprecated && <Badge variant="outline">已下架</Badge>}
                          </div>
                        </td>
                        <td className="hidden truncate px-2 py-2 font-mono text-xs text-muted-foreground @6xl/page:table-cell">{m.apiModelName}</td>
                        <td className="px-2 py-2 text-muted-foreground">{d ? MEDIA_LABEL[d.mediaType] : "—"}</td>
                        <td className="truncate px-2 py-2 text-xs tabular-nums text-muted-foreground">{d ? scaledSummary(d.price, effectiveMultiplier(provider, m)) : "未定价"}</td>
                        <td className="px-3 py-2">
                          <input type="radio" aria-label={`设为默认 ${m.apiModelName}`} className="accent-primary" checked={m.isDefault} onChange={() => setDefault(provider.id, m.key)} />
                        </td>
                      </tr>
                      {userOwned && expanded === m.key && (
                        <tr>
                          <td colSpan={6} className="bg-muted/20 px-6 py-4">
                            <div className="flex max-w-178 flex-col gap-4">
                              <div className="grid grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-3 text-sm">
                                <FieldLabel>引用条目</FieldLabel>
                                <EntryPicker value={m.entryId} onChange={(id) => update(provider.id, m.key, { entryId: id })} />
                              </div>
                              {d?.mediaType === "video" && (
                                <div className="flex flex-col gap-2">
                                  <span className="text-sm font-medium">能力覆盖</span>
                                  <CapabilityOverrideEditor entry={d} endpoint={undefined} value={m.capabilityOverride} onChange={(c) => update(provider.id, m.key, { capabilityOverride: c })} />
                                </div>
                              )}
                              <p className="text-xs text-subtle-foreground">这个模型不在官方清单里，由你负责。官方日后收录同一 API 模型名时，会换成官方版本，只保留启用与默认状态。</p>
                              <div>
                                <Button variant="ghost" size="sm" className="text-destructive" onClick={() => remove(provider.id, m.key)}>
                                  <Trash2 data-icon="inline-start" />
                                  移除
                                </Button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>
      </div>
      <DiscoverDialog provider={provider} open={discoverOpen} onOpenChange={setDiscoverOpen} onAdded={(key) => setExpanded(key)} />
    </DetailPane>
  );
}

function DiscoverDialog({ provider, open, onOpenChange, onAdded }: { provider: Provider; open: boolean; onOpenChange: (v: boolean) => void; onAdded: (key: string) => void }) {
  const entries = useProto((s) => s.entries);
  const add = useProto((s) => s.addProviderModel);
  const listed = new Map(provider.models.map((m) => [m.apiModelName, m]));
  // 未收录的排在前面：这是用户打开这个对话框要找的东西
  const ids = [...(provider.discovered ?? [])].sort((a, b) => Number(listed.has(a)) - Number(listed.has(b)));
  const matchEntry = (id: string) => entries.find((e) => {
    const d = effectiveOf(e);
    return d.name === id || (d.aliases ?? []).includes(id);
  });
  const notReturned = provider.models.filter((m) => m.responsibility === "official" && !ids.includes(m.apiModelName));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>获取模型</DialogTitle>
          <DialogDescription>用当前密钥向 {provider.displayName} 查询到 {ids.length} 个模型。结果因账号和地域而异，没返回的模型不代表不能用。</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <ul className="flex flex-col divide-y divide-border text-sm">
            {ids.map((id) => {
              const pm = listed.get(id);
              const match = matchEntry(id);
              return (
                <li key={id} className="flex items-center gap-3 py-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">{id}</span>
                  {pm ? (
                    <span className="text-xs text-subtle-foreground">{pm.responsibility === "official" ? "官方收录" : "已添加"}</span>
                  ) : (
                    <>
                      <span className="text-xs text-muted-foreground">{match ? `未收录 · 对应「${effectiveOf(match).displayName}」` : "未收录 · 无对应条目"}</span>
                      <Button
                        variant="outline"
                        size="xs"
                        onClick={() => {
                          const key = `${provider.id}:${id}`;
                          add(provider.id, { key, entryId: match?.id ?? null, apiModelName: id, enabled: true, isDefault: false, responsibility: "user", multiplier: 1 });
                          onAdded(key);
                        }}
                      >
                        <Plus data-icon="inline-start" />
                        添加
                      </Button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          {notReturned.length > 0 && (
            <p className="mt-3 text-xs text-subtle-foreground">
              官方清单里另有 {notReturned.length} 个模型这次没有返回（{notReturned.map((m) => m.apiModelName).join("、")}），仍保留在列表中。
            </p>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

// ---------- 自定义供应商 ----------

type Draft = Pick<Provider, "baseUrl" | "multiplier" | "models">;

function CustomDetail({ provider }: { provider: Provider }) {
  const entries = useProto((s) => s.entries);
  const updateProvider = useProto((s) => s.updateProvider);
  const source = useMemo<Draft>(() => ({ baseUrl: provider.baseUrl, multiplier: provider.multiplier, models: provider.models }), [provider]);
  const save = useCallback(async (v: Draft) => updateProvider(provider.id, v), [provider.id, updateProvider]);
  const unit = useEditUnit({ source, save });
  const draft = unit.value;
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [dropped, setDropped] = useState<Record<string, number>>({});
  const setModel = (key: string, patch: Partial<ProviderModel>) =>
    unit.setValue((prev) => ({
      ...prev,
      models: prev.models.map((m) => {
        if (m.key !== key) return m;
        if (patch.endpoint && patch.endpoint !== m.endpoint) {
          const n = Object.keys(m.pathOverrides ?? {}).length;
          if (n) setDropped((s) => ({ ...s, [key]: n }));
          return { ...m, ...patch, pathOverrides: {} };
        }
        return { ...m, ...patch };
      }),
    }));
  const toggle = (key: string) => setExpanded((s) => {
    const n = new Set(s);
    if (n.has(key)) n.delete(key);
    else n.add(key);
    return n;
  });

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-3">
          <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-sm text-subtle-foreground">{Array.from(provider.displayName)[0]}</span>
          <div className="flex min-w-0 flex-col gap-1">
            <h2 className="text-lg font-medium">{provider.displayName}</h2>
            <p className="truncate font-mono text-xs text-subtle-foreground">{draft.baseUrl}</p>
          </div>
        </div>
      }
      footer={<SaveBar unit={unit} className="max-w-[62.5rem]" />}
    >
      <div className="flex max-w-[64rem] flex-col gap-8 px-6 py-6">
        <Section title="连接">
          <div className="grid max-w-178 grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-3 text-sm">
            <FieldLabel htmlFor="a-base">base_url</FieldLabel>
            <Input id="a-base" className="font-mono text-xs" value={draft.baseUrl ?? ""} onChange={(e) => unit.setValue((p) => ({ ...p, baseUrl: e.target.value }))} />
            <FieldLabel htmlFor="a-pmul">供应商倍率</FieldLabel>
            <div className="flex items-center gap-3">
              <Input id="a-pmul" inputMode="decimal" className="w-24 text-right tabular-nums" value={String(draft.multiplier)} onChange={(e) => unit.setValue((p) => ({ ...p, multiplier: Number(e.target.value) || 0 }))} />
              <span className="text-xs text-subtle-foreground">乘在这个供应商下所有模型的参考费用上，如充值折扣 0.8</span>
            </div>
          </div>
        </Section>

        <Section title="供应商模型" hint="价格来自引用的模型条目，渠道价差用倍率表达。">
          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full table-fixed text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="w-14 px-3 py-2 text-left font-normal">启用</th>
                  <th className="px-2 py-2 text-left font-normal">API 模型名</th>
                  <th className="w-48 px-2 py-2 text-left font-normal">引用条目</th>
                  <th className="hidden w-40 px-2 py-2 text-left font-normal @6xl/page:table-cell">调用端点</th>
                  <th className="w-52 px-2 py-2 text-left font-normal">参考价</th>
                  <th className="w-14 px-3 py-2 text-left font-normal">默认</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {draft.models.map((m) => {
                  const e = entryOf(entries, m.entryId);
                  const d = e ? effectiveOf(e) : null;
                  const open = expanded.has(m.key);
                  const mult = Number((draft.multiplier * m.multiplier).toFixed(4));
                  const overrides = Object.keys(m.pathOverrides ?? {}).length;
                  return (
                    <Fragment key={m.key}>
                      <tr className={cn("cursor-pointer hover:bg-muted/30", !m.enabled && "text-muted-foreground")} onClick={() => toggle(m.key)}>
                        <td className="px-3 py-2" onClick={(ev) => ev.stopPropagation()}>
                          <Switch size="sm" checked={m.enabled} onCheckedChange={(on) => setModel(m.key, { enabled: on })} aria-label={`启用 ${m.apiModelName}`} />
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex items-center gap-2">
                            {open ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
                            <span className="truncate font-mono text-xs">{m.apiModelName || "（未填写）"}</span>
                            {overrides > 0 && <span className="shrink-0 text-xs text-subtle-foreground">路径覆盖 {overrides}</span>}
                          </div>
                        </td>
                        <td className="truncate px-2 py-2">{d ? d.displayName : <span className="text-warn">未对应条目</span>}</td>
                        <td className="hidden truncate px-2 py-2 text-muted-foreground @6xl/page:table-cell">{m.endpoint ? ENDPOINTS[m.endpoint].label : "—"}</td>
                        <td className="truncate px-2 py-2 text-xs tabular-nums text-muted-foreground">{d ? scaledSummary(d.price, mult) : <span className="text-warn">未定价</span>}</td>
                        <td className="px-3 py-2" onClick={(ev) => ev.stopPropagation()}>
                          <input
                            type="radio"
                            aria-label={`设为默认 ${m.apiModelName}`}
                            className="accent-primary"
                            checked={m.isDefault}
                            onChange={() =>
                              unit.setValue((p) => {
                                const media = d?.mediaType;
                                const mediaOf = (x: ProviderModel) => {
                                  const xe = entryOf(entries, x.entryId);
                                  return xe ? effectiveOf(xe).mediaType : undefined;
                                };
                                return { ...p, models: p.models.map((x) => (x.key === m.key ? { ...x, isDefault: true, enabled: true } : mediaOf(x) === media ? { ...x, isDefault: false } : x)) };
                              })
                            }
                          />
                        </td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={6} className="bg-muted/20 px-6 py-5">
                            <ModelEditor
                              model={m}
                              entryData={d}
                              baseUrl={draft.baseUrl ?? ""}
                              providerMultiplier={draft.multiplier}
                              droppedOverrides={dropped[m.key]}
                              onChange={(patch) => setModel(m.key, patch)}
                              onRemove={() => unit.setValue((p) => ({ ...p, models: p.models.filter((x) => x.key !== m.key) }))}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            <div className="border-t border-border px-3 py-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  const key = `${provider.id}:new-${Date.now()}`;
                  unit.setValue((p) => ({ ...p, models: [...p.models, { key, entryId: null, apiModelName: "", enabled: true, isDefault: false, responsibility: "user", endpoint: "openai-chat", multiplier: 1 }] }));
                  setExpanded((s) => new Set(s).add(key));
                }}
              >
                <Plus data-icon="inline-start" />
                添加模型
              </Button>
            </div>
          </div>
        </Section>
      </div>
    </DetailPane>
  );
}

function ModelEditor({
  model,
  entryData,
  baseUrl,
  providerMultiplier,
  droppedOverrides,
  onChange,
  onRemove,
}: {
  model: ProviderModel;
  entryData: ReturnType<typeof effectiveOf> | null;
  baseUrl: string;
  providerMultiplier: number;
  droppedOverrides?: number;
  onChange: (patch: Partial<ProviderModel>) => void;
  onRemove: () => void;
}) {
  const endpointOptions = (Object.keys(ENDPOINTS) as EndpointKey[])
    .filter((k) => !entryData || ENDPOINTS[k].mediaType === entryData.mediaType || k === model.endpoint)
    .map((k) => ({ value: k, label: `${ENDPOINTS[k].label}${ENDPOINTS[k].kind === "declarative" ? "（声明式）" : ""}` }));
  const hasOverrides = Object.keys(model.pathOverrides ?? {}).length > 0;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid max-w-178 grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-3 text-sm">
        <FieldLabel>引用条目</FieldLabel>
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <EntryPicker value={model.entryId} onChange={(id) => onChange({ entryId: id })} />
          </div>
          {model.entryId && (
            <Link href={modelsPath(model.entryId)} className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              在模型目录中查看
              <ExternalLink className="size-3" />
            </Link>
          )}
        </div>
        {!model.entryId && (
          <>
            <span />
            <span className="-mt-1 text-xs text-warn">未对应条目：调用记为未定价。可在模型目录里新建条目后回来选择。</span>
          </>
        )}
        <FieldLabel>API 模型名</FieldLabel>
        <Input className="font-mono text-xs" value={model.apiModelName} onChange={(e) => onChange({ apiModelName: e.target.value })} placeholder="发给供应商的模型名" />
        <FieldLabel>调用端点</FieldLabel>
        <div className="flex flex-col gap-1">
          <SimpleSelect label="调用端点" value={model.endpoint ?? ""} options={endpointOptions} onChange={(v) => onChange({ endpoint: v as EndpointKey })} className="w-64" />
          {hasOverrides && <span className="text-xs text-subtle-foreground">改挂端点时，下面的路径覆盖会被丢弃。</span>}
          {!!droppedOverrides && !hasOverrides && <span className="text-xs text-warn">已丢弃原端点的 {droppedOverrides} 条路径覆盖。</span>}
        </div>
        <FieldLabel>模型倍率</FieldLabel>
        <div className="flex items-center gap-3">
          <Input inputMode="decimal" className="w-24 text-right tabular-nums" value={String(model.multiplier)} onChange={(e) => onChange({ multiplier: Number(e.target.value) || 0 })} />
          <span className="text-xs text-subtle-foreground tabular-nums">
            生效倍率 = 供应商 {providerMultiplier} × 模型 {model.multiplier} = {Number((providerMultiplier * model.multiplier).toFixed(4))}
          </span>
        </div>
      </div>

      {entryData?.mediaType === "video" && (
        <div className="flex max-w-178 flex-col gap-2">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium">能力覆盖</span>
            <span className="text-xs text-muted-foreground">只在这个渠道和条目或端点声明不一致时覆盖，每项整体替换。</span>
          </div>
          <CapabilityOverrideEditor entry={entryData} endpoint={model.endpoint} value={model.capabilityOverride} onChange={(c) => onChange({ capabilityOverride: c })} />
        </div>
      )}

      {model.endpoint && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium">路径覆盖</span>
            <span className="text-xs text-muted-foreground">中转站改了路径时，按请求整条替换，相对于 base_url 原样值。留空沿用端点默认路径。</span>
          </div>
          <PathOverrideTable baseUrl={baseUrl} endpoint={model.endpoint} apiModelName={model.apiModelName} value={model.pathOverrides} onChange={(p) => onChange({ pathOverrides: p })} />
        </div>
      )}

      <div>
        <Button variant="ghost" size="sm" className="text-destructive" onClick={onRemove}>
          <Trash2 data-icon="inline-start" />
          移除这个模型
        </Button>
      </div>
    </div>
  );
}
