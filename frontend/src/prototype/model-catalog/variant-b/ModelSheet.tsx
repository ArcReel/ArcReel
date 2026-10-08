// PROTOTYPE — #3091 变体 B 的供应商模型 Sheet。上下两段明确区分「归谁」：
// 上段是本供应商的设置（渠道事实），下段是所有供应商共用的模型条目。两段共用一份草稿和一条保存栏。

import { useCallback, useMemo, useState } from "react";
import { ArrowLeftRight, Info, Link2, Trash2 } from "lucide-react";
import { cn } from "cn";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  CAPABILITY_LABEL,
  ENDPOINTS,
  MEDIA_LABEL,
  diffEntries,
  effectiveMultiplier,
  effectiveOf,
  makerLabel,
  officialOf,
  statusOf,
  type Capability,
  type EndpointKey,
  type EntryData,
  type Provider,
  type ProviderModel,
} from "../model";
import { referencesOf, useProto } from "../store";
import { EntryPicker, NewEntryDialog } from "./AddModel";
import { CapabilityOverrides } from "./CapabilityOverrides";
import { FlagBadge, OfficialHint, SectionTitle, StatusBadge, flagsOf } from "./bits";
import { PathBars } from "./PathBar";
import { PriceCards } from "./PriceCards";

interface Draft {
  model: ProviderModel;
  entry: EntryData | null;
}

const CAPS_BY_MEDIA: Record<string, Capability[]> = {
  text: ["text_generation", "structured_output", "vision"],
  image: ["text_to_image", "image_to_image"],
  audio: ["text_to_speech"],
  video: [],
};

export function ModelSheet({ provider, model, onClose }: { provider: Provider; model: ProviderModel; onClose: () => void }) {
  const entries = useProto((s) => s.entries);
  const providers = useProto((s) => s.providers);
  const updateProviderModel = useProto((s) => s.updateProviderModel);
  const saveEntry = useProto((s) => s.saveEntry);
  const setEntrySync = useProto((s) => s.setEntrySync);
  const removeProviderModel = useProto((s) => s.removeProviderModel);
  const setDefault = useProto((s) => s.setDefault);

  const storeEntry = entries.find((e) => e.id === model.entryId);
  const source = useMemo<Draft>(() => ({ model, entry: storeEntry ? effectiveOf(storeEntry) : null }), [model, storeEntry]);

  const save = useCallback(
    async (value: Draft) => {
      const { isDefault, ...rest } = value.model;
      updateProviderModel(provider.id, model.key, rest);
      if (isDefault && !model.isDefault) setDefault(provider.id, model.key);
      if (!isDefault && model.isDefault) updateProviderModel(provider.id, model.key, { isDefault: false });
      const target = useProto.getState().entries.find((e) => e.id === value.model.entryId);
      if (value.entry && target && JSON.stringify(value.entry) !== JSON.stringify(effectiveOf(target))) saveEntry(target.id, value.entry);
    },
    [provider.id, model.key, model.isDefault, updateProviderModel, saveEntry, setDefault],
  );
  const unit = useEditUnit<Draft>({ source, save });
  const draft = unit.value;
  const setModel = (patch: Partial<ProviderModel>) => unit.setValue((d) => ({ ...d, model: { ...d.model, ...patch } }));
  const setEntry = (patch: Partial<EntryData>) => unit.setValue((d) => (d.entry ? { ...d, entry: { ...d.entry, ...patch } } : d));

  const draftCatalogEntry = entries.find((e) => e.id === draft.model.entryId);
  const entryDirty = !!draft.entry && !!draftCatalogEntry && JSON.stringify(draft.entry) !== JSON.stringify(effectiveOf(draftCatalogEntry));

  const official = draftCatalogEntry ? officialOf(draftCatalogEntry) : null;
  const status = draftCatalogEntry ? statusOf(draftCatalogEntry) : null;
  const userOwnsEntry = draftCatalogEntry ? !draftCatalogEntry.syncOn || entryDirty : false;
  const diffs = useMemo(() => (official && draft.entry && userOwnsEntry ? diffEntries(official, draft.entry) : []), [official, draft.entry, userOwnsEntry]);
  const diffOf = (field: string) => diffs.find((d) => d.field === field);
  const adopt = (field: keyof EntryData) => official && setEntry({ [field]: structuredClone(official[field]) });

  const readOnlyChannel = model.responsibility === "official";
  const refs = draft.model.entryId ? referencesOf(providers, draft.model.entryId) : [];
  const [pickEntry, setPickEntry] = useState(false);
  const [creating, setCreating] = useState(false);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const endpointChangedWithOverrides =
    provider.kind === "custom" && draft.model.endpoint !== model.endpoint && Object.keys(model.pathOverrides ?? {}).length > 0;

  const title = draft.entry?.displayName ?? draft.model.apiModelName;
  const flags = flagsOf(model, storeEntry);

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="data-[side=right]:w-[720px]">
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-2">
            {title}
            {flags.map((f) => (
              <FlagBadge key={f} flag={f} />
            ))}
          </SheetTitle>
          <SheetDescription>
            {provider.displayName} · <span className="font-mono">{draft.model.apiModelName}</span>
          </SheetDescription>
        </SheetHeader>

        <SheetBody className="flex flex-col gap-0 p-0">
          {/* 上段：本供应商的设置 */}
          <section className="flex flex-col gap-4 px-5 py-5">
            <div className="flex items-center gap-2">
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-subtle-foreground">只影响 {provider.displayName}</span>
              <h3 className="text-base font-medium">本供应商的设置</h3>
            </div>
            {readOnlyChannel && (
              <p className="flex items-start gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs text-subtle-foreground">
                <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                官方收录，全部跟随官方数据；只能启用、停用、设为默认。
              </p>
            )}
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={draft.model.enabled} onCheckedChange={(v) => setModel({ enabled: v, ...(v ? {} : { isDefault: false }) })} />
                启用
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={draft.model.isDefault} onCheckedChange={(v) => setModel({ isDefault: v, ...(v ? { enabled: true } : {}) })} />
                设为{draft.entry ? MEDIA_LABEL[draft.entry.mediaType] : ""}默认
              </label>
            </div>

            <Field label="API 模型名" hint="实际发给供应商的模型名，可以与条目的官方模型名不同">
              <Input
                className="font-mono"
                readOnly={readOnlyChannel}
                value={draft.model.apiModelName}
                onChange={(e) => setModel({ apiModelName: e.target.value })}
              />
            </Field>

            {provider.kind === "custom" && (
              <Field label="调用端点">
                <Select
                  value={draft.model.endpoint ?? null}
                  items={Object.values(ENDPOINTS).map((e) => ({ value: e.key, label: `${e.label}（${MEDIA_LABEL[e.mediaType]}）` }))}
                  onValueChange={(v) => v && setModel({ endpoint: v, pathOverrides: {} })}
                >
                  <SelectTrigger className="w-72">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.values(ENDPOINTS).map((e) => (
                      <SelectItem key={e.key} value={e.key}>
                        {e.label}（{MEDIA_LABEL[e.mediaType]}）{e.kind === "declarative" ? " · 声明式" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {endpointChangedWithOverrides && <p className="mt-1 text-xs text-warn">改挂端点后，原有路径覆盖将丢弃。</p>}
              </Field>
            )}

            <Field label="倍率" hint={readOnlyChannel ? "官方收录的供应商模型倍率随官方数据下发" : "供应商模型倍率，乘在整笔参考费用上"}>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  step="0.05"
                  min={0}
                  className="w-24 tabular-nums"
                  readOnly={readOnlyChannel}
                  value={draft.model.multiplier}
                  onChange={(e) => setModel({ multiplier: Number(e.target.value) })}
                />
                <span className="text-xs text-muted-foreground">
                  生效倍率 = 供应商 ×{provider.multiplier} × 本模型 ×{draft.model.multiplier} = ×{effectiveMultiplier(provider, draft.model)}
                </span>
              </div>
            </Field>

            {!readOnlyChannel && (draft.entry?.mediaType === "video" || draft.entry?.mediaType === "image") && (
              <Field label="能力覆盖" hint="渠道与条目或端点声明不一致时用覆盖表达，按维度整体替换，优先级最高">
                <CapabilityOverrides
                  entry={draft.entry}
                  endpoint={draft.model.endpoint}
                  channelSource={provider.kind === "custom" ? "端点声明" : "内置通道"}
                  value={draft.model.capabilityOverride}
                  onChange={(v) => setModel({ capabilityOverride: v })}
                />
              </Field>
            )}

            {provider.kind === "custom" && draft.model.endpoint && (
              <Field label="请求路径" hint="点击路径段修改。覆盖整条替换路径，相对于 base_url 原样值">
                <PathBarsLazy provider={provider} draft={draft} onChange={(v) => setModel({ pathOverrides: v })} />
              </Field>
            )}

            {model.responsibility === "user" && (
              <Button
                variant="ghost"
                size="sm"
                className="self-start text-destructive"
                onClick={() => {
                  removeProviderModel(provider.id, model.key);
                  onClose();
                }}
              >
                <Trash2 aria-hidden />从 {provider.displayName} 移除
              </Button>
            )}
          </section>

          {/* 下段：模型条目 */}
          <section className="flex flex-col gap-4 border-t-4 border-border bg-card/30 px-5 py-5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-primary/15 px-1.5 py-0.5 text-xs text-primary">所有供应商共用</span>
              <h3 className="text-base font-medium">模型条目</h3>
              {status && <StatusBadge status={status} />}
              {!readOnlyChannel && (
                <Button variant="ghost" size="xs" className="ml-auto" onClick={() => setPickEntry(true)}>
                  <ArrowLeftRight aria-hidden />
                  换一个条目
                </Button>
              )}
            </div>

            {!draft.entry || !draftCatalogEntry ? (
              <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-warn/50 px-4 py-4">
                <p className="text-sm text-warn">这个供应商模型没有对应到模型条目，调用记为未定价。</p>
                <p className="text-xs text-muted-foreground">选一个目录里的条目，能力与价格就从条目取；目录里没有就新建一个。</p>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setPickEntry(true)}>
                    选择条目
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
                    新建条目
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <span className="font-mono text-subtle-foreground">{draftCatalogEntry.id}</span>
                  <span className="text-muted-foreground">
                    {makerLabel(draft.entry.maker)} · {MEDIA_LABEL[draft.entry.mediaType]}
                  </span>
                </div>

                <p className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/[0.07] px-3 py-2 text-xs text-subtle-foreground">
                  <Link2 aria-hidden className="mt-0.5 size-3.5 shrink-0 text-primary" />
                  <span>
                    修改会影响 {refs.length} 个供应商模型：
                    {refs.map((r, i) => (
                      <span key={r.model.key}>
                        {i > 0 && "、"}
                        <span className={cn(r.model.key === model.key && "text-foreground")}>
                          {r.provider.displayName}
                          {refs.filter((x) => x.provider.id === r.provider.id).length > 1 && `（${r.model.apiModelName}）`}
                        </span>
                      </span>
                    ))}
                  </span>
                </p>

                <SyncRow
                  entryStatus={status!}
                  syncOn={draftCatalogEntry.syncOn}
                  hasOfficial={!!official}
                  onTurnOff={() => setEntrySync(draftCatalogEntry.id, false)}
                  onRestore={() => setConfirmRestore(true)}
                />
                {status === "modified-outdated" && (
                  <p className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
                    官方在你修改后更新过（官方 {official?.version}，你的版本基于 {draftCatalogEntry.user?.version}）。不同之处已在各字段下方标出，可以逐项采用，或恢复官方值。
                  </p>
                )}
                {status === "user-created-official" && (
                  <p className="rounded-md border border-good/40 bg-good/10 px-3 py-2 text-xs text-good">
                    官方已收录同一身份的条目。打开官方同步即改用官方数据，你的版本会丢弃。
                  </p>
                )}

                <Field label="显示名">
                  <Input value={draft.entry.displayName} onChange={(e) => setEntry({ displayName: e.target.value })} />
                  {diffOf("displayName") && <OfficialHint value={diffOf("displayName")!.before} onAdopt={() => adopt("displayName")} />}
                </Field>

                {CAPS_BY_MEDIA[draft.entry.mediaType].length > 0 && (
                  <Field label="能力" hint="模型本身的事实；渠道差异用上方的能力覆盖">
                    <div className="flex flex-wrap gap-1.5">
                      {CAPS_BY_MEDIA[draft.entry.mediaType].map((c) => {
                        const on = draft.entry!.capabilities.includes(c);
                        return (
                          <button
                            key={c}
                            type="button"
                            aria-pressed={on}
                            onClick={() => setEntry({ capabilities: on ? draft.entry!.capabilities.filter((x) => x !== c) : [...draft.entry!.capabilities, c] })}
                            className={cn("h-7 rounded-md border px-2 text-xs", on ? "border-primary/50 bg-primary/15" : "border-border text-muted-foreground")}
                          >
                            {CAPABILITY_LABEL[c]}
                          </button>
                        );
                      })}
                    </div>
                    {diffOf("capabilities") && <OfficialHint value={diffOf("capabilities")!.before} onAdopt={() => adopt("capabilities")} />}
                  </Field>
                )}

                {draft.entry.mediaType === "video" && (
                  <Field label="可选时长">
                    <ListInput value={draft.entry.durations} suffix="秒" numeric onChange={(v) => setEntry({ durations: v as number[] })} />
                    {diffOf("durations") && <OfficialHint value={diffOf("durations")!.before} onAdopt={() => adopt("durations")} />}
                  </Field>
                )}
                {(draft.entry.mediaType === "video" || draft.entry.mediaType === "image") && (
                  <Field label="分辨率">
                    <ListInput value={draft.entry.resolutions} onChange={(v) => setEntry({ resolutions: v as string[] })} />
                    {diffOf("resolutions") && <OfficialHint value={diffOf("resolutions")!.before} onAdopt={() => adopt("resolutions")} />}
                  </Field>
                )}
                {draft.entry.mediaType === "text" && (
                  <Field label="最大输出">
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        className="w-32 tabular-nums"
                        value={draft.entry.maxOutputTokens ?? ""}
                        onChange={(e) => setEntry({ maxOutputTokens: e.target.value ? Number(e.target.value) : null })}
                      />
                      <span className="text-xs text-muted-foreground">token</span>
                    </div>
                    {diffOf("maxOutputTokens") && <OfficialHint value={diffOf("maxOutputTokens")!.before} onAdopt={() => adopt("maxOutputTokens")} />}
                  </Field>
                )}

                <Field label="价目表" hint="费用 = 倍率 × Σ 计量项（用量 − 免费额度）× 单价">
                  <PriceCards value={draft.entry.price} onChange={(price) => setEntry({ price })} official={userOwnsEntry ? official?.price : null} />
                </Field>
              </>
            )}
          </section>
        </SheetBody>

        <SheetFooter className="flex-col items-stretch gap-2">
          {entryDirty && draftCatalogEntry?.syncOn && (
            <p className="text-xs text-warn">保存后将关闭这个条目的官方同步，{refs.length} 个供应商模型都改用你的版本。</p>
          )}
          <SaveBar unit={unit} />
        </SheetFooter>
      </SheetContent>

      <EntryPicker
        open={pickEntry}
        onOpenChange={setPickEntry}
        media={draft.entry?.mediaType}
        onPick={(id) => {
          const e = entries.find((x) => x.id === id);
          unit.setValue((d) => ({ model: { ...d.model, entryId: id }, entry: e ? effectiveOf(e) : null }));
        }}
        onCreate={() => {
          setPickEntry(false);
          setCreating(true);
        }}
      />
      <NewEntryDialog
        open={creating}
        initialName={draft.model.apiModelName}
        onOpenChange={setCreating}
        onCreated={(data) => {
          setCreating(false);
          unit.setValue((d) => ({ model: { ...d.model, entryId: data.id }, entry: data }));
        }}
      />
      <AlertDialog open={confirmRestore} onOpenChange={setConfirmRestore}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>恢复官方值？</AlertDialogTitle>
            <AlertDialogDescription>
              你对这个条目的修改会丢弃，{refs.length} 个供应商模型改用官方数据，之后继续接收官方同步。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (draftCatalogEntry) setEntrySync(draftCatalogEntry.id, true);
                setConfirmRestore(false);
              }}
            >
              恢复官方值
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sheet>
  );
}

function SyncRow({
  entryStatus,
  syncOn,
  hasOfficial,
  onTurnOff,
  onRestore,
}: {
  entryStatus: string;
  syncOn: boolean;
  hasOfficial: boolean;
  onTurnOff: () => void;
  onRestore: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5">
      <Switch checked={syncOn} disabled={!hasOfficial} onCheckedChange={(v) => (v ? onRestore() : onTurnOff())} aria-label="官方同步" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-sm">官方同步</span>
        <span className="text-xs text-muted-foreground">
          {!hasOfficial
            ? "官方没有收录这个条目，由你负责"
            : syncOn
              ? entryStatus === "factory"
                ? "跟随官方数据，当前用的是出厂快照"
                : "跟随官方数据，有更新时自动应用"
              : "已关闭：这个条目由你负责，官方更新不再应用"}
        </span>
      </div>
      {!syncOn && hasOfficial && (
        <Button variant="outline" size="sm" onClick={onRestore}>
          恢复官方值
        </Button>
      )}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <SectionTitle title={label} hint={hint} />
      <div>{children}</div>
    </div>
  );
}

function ListInput({ value, onChange, suffix, numeric }: { value: (string | number)[]; onChange: (v: (string | number)[]) => void; suffix?: string; numeric?: boolean }) {
  const [text, setText] = useState(value.join(", "));
  const [last, setLast] = useState(value);
  if (last !== value && JSON.stringify(last) !== JSON.stringify(value)) {
    setLast(value);
    setText(value.join(", "));
  }
  return (
    <div className="flex items-center gap-2">
      <Input
        className="w-72"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const parts = e.target.value.split(/[,，\s]+/).filter(Boolean);
          const next = numeric ? parts.map(Number).filter((n) => n > 0) : parts;
          setLast(next);
          onChange(next);
        }}
      />
      <span className="text-xs text-muted-foreground">{suffix ? `${suffix}，` : ""}用逗号分隔</span>
    </div>
  );
}

function PathBarsLazy({ provider, draft, onChange }: { provider: Provider; draft: Draft; onChange: (v: Record<string, string>) => void }) {
  return (
    <PathBars
      baseUrl={provider.baseUrl ?? ""}
      endpoint={draft.model.endpoint!}
      apiModelName={draft.model.apiModelName}
      overrides={draft.model.pathOverrides ?? {}}
      onChange={onChange}
    />
  );
}
