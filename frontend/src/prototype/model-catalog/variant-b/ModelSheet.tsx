// PROTOTYPE — #3091 变体 B 的供应商模型 Sheet。上下两段明确区分「归谁」：
// 上段是本供应商的设置（渠道事实），下段是所有供应商共用的模型条目（界面上叫「模型信息」）。两段共用一份草稿和一条保存栏。

import { useCallback, useMemo, useState } from "react";
import { ArrowLeftRight, Link2, Trash2 } from "lucide-react";
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
  type EntryData,
  type EntryStatus,
  type Provider,
  type ProviderModel,
} from "../model";
import { referencesOf, useProto } from "../store";
import { EntryPicker, NewEntryDialog } from "./AddModel";
import { CapabilityOverrides } from "./CapabilityOverrides";
import { FlagBadge, OfficialHint, SectionTitle, endpointLabel, flagsOf } from "./bits";
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
  // 模型信息自身的状态（已修改、自建）在下段说明，标题旁只留影响调用的提示
  const flags = flagsOf(model, storeEntry).filter((f) => f !== "modified" && f !== "user-created");
  // 同样使用这份模型信息的其他供应商模型；只有一个时不提示
  const others = refs.filter((r) => r.model.key !== model.key);
  const othersText = others.map((r) => `「${r.provider.displayName}」`).filter((v, i, a) => a.indexOf(v) === i).join("");

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
          <SheetDescription>{provider.displayName}</SheetDescription>
        </SheetHeader>

        <SheetBody className="flex flex-col gap-0 p-0">
          {/* 上段：只属于这个供应商的设置 */}
          <section className="flex flex-col gap-4 px-5 py-5">
            <h3 className="text-base font-medium">供应商设置</h3>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={draft.model.enabled} onCheckedChange={(v) => setModel({ enabled: v, ...(v ? {} : { isDefault: false }) })} />
                启用
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch checked={draft.model.isDefault} onCheckedChange={(v) => setModel({ isDefault: v, ...(v ? { enabled: true } : {}) })} />
                设为默认{draft.entry ? MEDIA_LABEL[draft.entry.mediaType] : ""}模型
              </label>
            </div>
            {readOnlyChannel && <p className="-mt-2 text-xs text-muted-foreground">内置模型的其他设置由官方维护。</p>}

            {!readOnlyChannel && (
              <Field label="模型 ID" hint="调用时发给供应商的模型名称。">
                <Input className="font-mono" value={draft.model.apiModelName} onChange={(e) => setModel({ apiModelName: e.target.value })} />
              </Field>
            )}

            {provider.kind === "custom" && (
              <Field label="调用端点">
                <Select
                  value={draft.model.endpoint ?? null}
                  items={Object.values(ENDPOINTS).map((e) => ({ value: e.key, label: endpointLabel(e.key) }))}
                  onValueChange={(v) => v && setModel({ endpoint: v, pathOverrides: {} })}
                >
                  <SelectTrigger className="w-72">
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
                {endpointChangedWithOverrides && <p className="mt-1 text-xs text-warn">更换调用端点后，修改过的请求地址会恢复默认。</p>}
              </Field>
            )}

            {!readOnlyChannel && (
              <Field label="倍率">
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    step="0.05"
                    min={0}
                    className="w-24 tabular-nums"
                    value={draft.model.multiplier}
                    onChange={(e) => setModel({ multiplier: Number(e.target.value) })}
                  />
                  {provider.multiplier !== 1 && (
                    <span className="text-xs text-muted-foreground">
                      再乘供应商倍率 {provider.multiplier}，最终为 {effectiveMultiplier(provider, draft.model)}
                    </span>
                  )}
                </div>
              </Field>
            )}

            {!readOnlyChannel && (draft.entry?.mediaType === "video" || draft.entry?.mediaType === "image") && (
              <Field label="这个供应商支持的参数" hint="与下方模型信息不同时修改。">
                <CapabilityOverrides
                  entry={draft.entry}
                  endpoint={draft.model.endpoint}
                  value={draft.model.capabilityOverride}
                  onChange={(v) => setModel({ capabilityOverride: v })}
                />
              </Field>
            )}

            {provider.kind === "custom" && draft.model.endpoint && (
              <Field label="请求地址" hint="供应商的地址与默认不同时，点击路径部分修改。">
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
                <Trash2 aria-hidden />从「{provider.displayName}」移除
              </Button>
            )}
          </section>

          {/* 下段：模型信息，所有供应商共用 */}
          <section className="flex flex-col gap-4 border-t-4 border-border bg-card/30 px-5 py-5">
            <div className="flex items-center gap-2">
              <h3 className="min-w-0 flex-1 text-base font-medium">模型信息</h3>
              {!readOnlyChannel && draftCatalogEntry && (
                <Button variant="ghost" size="xs" onClick={() => setPickEntry(true)}>
                  <ArrowLeftRight aria-hidden />
                  关联其他模型
                </Button>
              )}
            </div>

            {!draft.entry || !draftCatalogEntry ? (
              <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-warn/50 px-4 py-4">
                <p className="text-sm">没有这个模型的能力和价格信息，调用费用会记为未定价。</p>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setPickEntry(true)}>
                    从模型目录选择
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
                    手动填写
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <OwnerNote
                  status={status!}
                  hasOfficial={!!official}
                  maker={makerLabel(draft.entry.maker)}
                  onRestore={() => setConfirmRestore(true)}
                />
                {others.length > 0 && (
                  <p className="flex items-start gap-2 text-xs text-muted-foreground">
                    <Link2 aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                    {othersText}也使用这些信息，修改会一起生效。
                  </p>
                )}

                <Field label="显示名称">
                  <Input value={draft.entry.displayName} onChange={(e) => setEntry({ displayName: e.target.value })} />
                  {diffOf("displayName") && <OfficialHint value={diffOf("displayName")!.before} onAdopt={() => adopt("displayName")} />}
                </Field>

                {CAPS_BY_MEDIA[draft.entry.mediaType].length > 0 && (
                  <Field label="能力">
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
                  <Field label="最大输出长度">
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

                <Field label="价格">
                  <PriceCards value={draft.entry.price} onChange={(price) => setEntry({ price })} official={userOwnsEntry ? official?.price : null} />
                </Field>
              </>
            )}
          </section>
        </SheetBody>

        <SheetFooter className="flex-col items-stretch gap-2">
          {entryDirty && draftCatalogEntry?.syncOn && (
            <p className="text-xs text-warn">
              保存后，这个模型改用你修改的信息，不再随官方更新。{others.length > 0 && `${othersText}也会一起改变。`}
            </p>
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
            <AlertDialogTitle>恢复官方数据</AlertDialogTitle>
            <AlertDialogDescription>
              你对「{draft.entry?.displayName}」所做的修改会被丢弃，无法撤销。{others.length > 0 && `${othersText}也会一起恢复。`}
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
              恢复官方数据
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sheet>
  );
}

/**
 * 模型信息由谁维护。跟随官方时只是一行说明；用户改过时给出「恢复官方数据」。
 * 不设同步开关：关掉而不修改没有意义，打开即恢复官方数据，一个按钮足够（保存修改时自动停止跟随官方）。
 */
function OwnerNote({ status, hasOfficial, maker, onRestore }: { status: EntryStatus; hasOfficial: boolean; maker: string; onRestore: () => void }) {
  if (status === "factory" || status === "synced") {
    return <p className="-mt-2 text-xs text-muted-foreground">{maker}的模型，信息由官方维护并自动更新。</p>;
  }
  const text = !hasOfficial
    ? "官方没有这个模型的信息，以下内容由你填写。"
    : status === "user-created" || status === "user-created-official"
      ? "官方现在也提供这个模型的信息，可以改用官方数据。"
      : "你修改过这些信息，官方更新不会覆盖。";
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5">
      <span className="min-w-0 flex-1 text-xs text-subtle-foreground">{text}</span>
      {hasOfficial && (
        <Button variant="outline" size="sm" onClick={onRestore}>
          {status === "user-created-official" ? "改用官方数据" : "恢复官方数据"}
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
      <span className="text-xs text-muted-foreground">{suffix ? `${suffix}，` : ""}多个值用逗号分隔</span>
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
