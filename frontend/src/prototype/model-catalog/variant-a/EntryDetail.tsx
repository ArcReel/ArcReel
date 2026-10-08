// PROTOTYPE — #3091 变体 A：模型条目详情栏。草稿走 useEditUnit，保存即生成用户副本并关闭官方同步。
import { useCallback, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Info, RotateCcw } from "lucide-react";
import { Link } from "wouter";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "cn";
import {
  CAPABILITY_LABEL,
  MEDIA_LABEL,
  diffEntries,
  effectiveMultiplier,
  effectiveOf,
  makerLabel,
  officialOf,
  statusOf,
  type Capability,
  type CatalogEntry,
  type EntryData,
  type MeterKey,
} from "../model";
import { referencesOf, useProto } from "../store";
import { PriceMatrix } from "./PriceMatrix";
import { FieldLabel, Section, StatusBadge, UnpricedBadge, providerPath, scaledSummary } from "./shared";

const CAPS_BY_MEDIA: Record<EntryData["mediaType"], Capability[]> = {
  text: ["text_generation", "structured_output", "vision"],
  image: ["text_to_image", "image_to_image"],
  video: [],
  audio: ["text_to_speech"],
};
const VIDEO_RESOLUTIONS = ["480p", "720p", "1080p", "4k"];

/** 把官方值的某个字段搬进草稿（「采用官方值」）。 */
function adoptField(draft: EntryData, official: EntryData, field: string): EntryData {
  if (field === "price.currency") return { ...draft, price: { ...draft.price, currency: official.price.currency } };
  if (field.startsWith("price.")) {
    const meter = field.slice(6) as MeterKey;
    const theirs = official.price.meters.find((m) => m.meter === meter);
    const others = draft.price.meters.filter((m) => m.meter !== meter);
    return { ...draft, price: { ...draft.price, meters: theirs ? [...others, structuredClone(theirs)] : others } };
  }
  return { ...draft, [field]: structuredClone(official[field as keyof EntryData]) };
}

export function EntryDetail({ entry }: { entry: CatalogEntry }) {
  const saveEntry = useProto((s) => s.saveEntry);
  const setEntrySync = useProto((s) => s.setEntrySync);
  const providers = useProto((s) => s.providers);
  const source = useMemo(() => effectiveOf(entry), [entry]);
  const save = useCallback(async (value: EntryData) => saveEntry(entry.id, value), [entry.id, saveEntry]);
  const unit = useEditUnit({ source, save });
  const draft = unit.value;
  const set = (patch: Partial<EntryData>) => unit.setValue((prev) => ({ ...prev, ...patch }));

  const status = statusOf(entry);
  const official = officialOf(entry);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [showDiff, setShowDiff] = useState(status === "modified-outdated");
  const refs = referencesOf(providers, entry.id);

  const diffs = useMemo(() => (official ? diffEntries(draft, official) : []), [draft, official]);
  // 跟随官方时：本次同步相对出厂快照改了什么
  const syncDiffs = useMemo(
    () => (entry.syncOn && entry.factory && entry.synced && entry.synced.version > entry.factory.version ? diffEntries(entry.factory, entry.synced) : []),
    [entry],
  );
  const restoreLabel = status === "user-created-official" ? "改用官方数据" : "恢复官方值";

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h2 className="truncate text-lg font-medium">{source.displayName}</h2>
              <StatusBadge entry={entry} />
              {source.price.meters.length === 0 && <UnpricedBadge />}
              {source.deprecated && <Badge variant="outline">官方已下架</Badge>}
            </div>
            <p className="truncate font-mono text-xs text-subtle-foreground">{entry.id}</p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <label className={cn("flex items-center gap-2 text-sm", !official && "text-subtle-foreground")}>
              官方同步
              <Switch
                checked={entry.syncOn}
                disabled={!official}
                onCheckedChange={(on) => {
                  if (on) setConfirmRestore(true);
                  else setEntrySync(entry.id, false);
                }}
              />
            </label>
            {!entry.syncOn && official && (
              <Button variant="outline" size="sm" onClick={() => setConfirmRestore(true)}>
                <RotateCcw data-icon="inline-start" />
                {restoreLabel}
              </Button>
            )}
          </div>
        </div>
      }
      footer={
        <div className="flex max-w-178 flex-col gap-2">
          {entry.syncOn && unit.dirty && (
            <p className="flex items-center gap-1.5 text-xs text-warn">
              <Info className="size-3.5" />
              保存后将关闭这个条目的官方同步，之后由你负责维护，不再接收官方更新。
            </p>
          )}
          <SaveBar unit={unit} />
        </div>
      }
    >
      <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
        {!official && (
          <Notice>这个条目由你新建，目录数据仓库里没有同一身份的条目。官方日后收录时，官方同步开关会变为可打开。</Notice>
        )}
        {status === "user-created-official" && (
          <Notice tone="warn" action={<DiffToggle open={showDiff} onToggle={() => setShowDiff((v) => !v)} count={diffs.length} />}>
            官方已收录同一身份的条目。打开官方同步即改用官方数据，你的条目会被丢弃。
          </Notice>
        )}
        {status === "modified-outdated" && (
          <Notice tone="warn" action={<DiffToggle open={showDiff} onToggle={() => setShowDiff((v) => !v)} count={diffs.length} />}>
            官方在你修改之后更新过这个条目（你的副本基于 {entry.user?.version}，官方现在是 {official?.version}）。官方更新没有应用到这里。
          </Notice>
        )}
        {status === "modified" && (
          <Notice action={<DiffToggle open={showDiff} onToggle={() => setShowDiff((v) => !v)} count={diffs.length} label="与官方值对比" />}>
            你修改过这个条目，官方同步已关闭。官方数据之后有更新也不会应用到这里。
          </Notice>
        )}
        {showDiff && official && status !== "factory" && status !== "synced" && (
          <DiffTable diffs={diffs} onAdopt={(field) => unit.setValue((prev) => adoptField(prev, official, field))} />
        )}
        {syncDiffs.length > 0 && (
          <Notice action={<DiffToggle open={showDiff} onToggle={() => setShowDiff((v) => !v)} count={syncDiffs.length} label="本次同步的变化" />}>
            官方同步 {entry.synced?.version} 更新了这个条目，已自动应用。
          </Notice>
        )}
        {showDiff && syncDiffs.length > 0 && <DiffTable diffs={syncDiffs} leftLabel="出厂快照" rightLabel="当前（官方同步）" />}
        {source.deprecated && <Notice>官方已下架这个模型。条目保留在目录里，已有的供应商模型仍按它计价。</Notice>}

        <Section title="基本信息">
          <div className="grid grid-cols-[8rem_1fr] items-center gap-x-4 gap-y-3 text-sm">
            <span className="text-muted-foreground">模型厂商</span>
            <span>{makerLabel(draft.maker)}</span>
            <span className="text-muted-foreground">媒体类型</span>
            <span>{MEDIA_LABEL[draft.mediaType]}</span>
            <FieldLabel htmlFor="a-display">显示名</FieldLabel>
            <Input id="a-display" value={draft.displayName} onChange={(e) => set({ displayName: e.target.value })} />
            <FieldLabel htmlFor="a-alias">别名</FieldLabel>
            <div className="flex flex-col gap-1">
              <Input
                id="a-alias"
                value={(draft.aliases ?? []).join(", ")}
                placeholder="如 claude-sonnet-4-5-20250929"
                onChange={(e) => set({ aliases: e.target.value.split(/[,，]\s*/).filter(Boolean) })}
              />
              <span className="text-xs text-subtle-foreground">获取模型返回的 id 与别名一致时，自动对应到这个条目</span>
            </div>
          </div>
        </Section>

        <Section
          title="能力"
          hint={draft.mediaType === "video" ? "这里只记模型本身的事实。输入模式、参考图上限与音轨形态由调用通道声明。" : "这里只记模型本身的事实；调用端点实现了什么由代码声明，生效能力取两者都满足的部分。"}
        >
          <div className="flex flex-col gap-3 text-sm">
            {CAPS_BY_MEDIA[draft.mediaType].length > 0 && (
              <div className="flex flex-wrap gap-x-5 gap-y-2">
                {CAPS_BY_MEDIA[draft.mediaType].map((c) => (
                  <label key={c} className="flex items-center gap-2">
                    <Checkbox
                      checked={draft.capabilities.includes(c)}
                      onCheckedChange={(on) => set({ capabilities: on ? [...draft.capabilities, c] : draft.capabilities.filter((x) => x !== c) })}
                    />
                    {CAPABILITY_LABEL[c]}
                  </label>
                ))}
              </div>
            )}
            {draft.mediaType === "text" && (
              <div className="flex items-center gap-3">
                <FieldLabel htmlFor="a-maxout">最大输出</FieldLabel>
                <Input
                  id="a-maxout"
                  inputMode="numeric"
                  className="w-32 text-right tabular-nums"
                  value={draft.maxOutputTokens ?? ""}
                  onChange={(e) => set({ maxOutputTokens: e.target.value ? Number(e.target.value) || null : null })}
                />
                <span className="text-xs text-subtle-foreground">token</span>
              </div>
            )}
            {draft.mediaType === "video" && (
              <>
                <div className="flex items-center gap-3">
                  <FieldLabel htmlFor="a-dur">可选时长</FieldLabel>
                  <Input
                    id="a-dur"
                    className="w-64"
                    value={draft.durations.join(", ")}
                    placeholder="如 5, 10"
                    onChange={(e) => set({ durations: e.target.value.split(/[,，\s]+/).map(Number).filter((n) => n > 0) })}
                  />
                  <span className="text-xs text-subtle-foreground">秒</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium">分辨率</span>
                  <div className="flex gap-1.5">
                    {VIDEO_RESOLUTIONS.map((r) => {
                      const on = draft.resolutions.includes(r);
                      return (
                        <button
                          key={r}
                          type="button"
                          aria-pressed={on}
                          onClick={() => set({ resolutions: on ? draft.resolutions.filter((x) => x !== r) : [...draft.resolutions, r] })}
                          className={cn(
                            "h-7 rounded-md border px-2.5 text-xs",
                            on ? "border-primary/50 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {r}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </>
            )}
          </div>
        </Section>

        <Section title="价目表" hint="费用 = 倍率 × Σ 计量项（用量 − 免费额度）× 单价。供应商之间的价差用倍率表达，不在这里改。">
          <PriceMatrix value={draft.price} onChange={(price) => set({ price })} />
        </Section>

        <Section title="被引用" hint={refs.length ? `${refs.length} 个供应商模型引用这个条目，修改会影响它们的参考费用。` : undefined}>
          {refs.length === 0 ? (
            <p className="text-sm text-muted-foreground">还没有供应商模型引用这个条目。</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
              {refs.map(({ provider, model }) => (
                <li key={model.key}>
                  <Link href={providerPath(provider.id)} className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-muted/50">
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate">{provider.displayName}</span>
                        <span className="shrink-0 text-xs text-subtle-foreground">{model.responsibility === "official" ? "官方收录" : "用户负责"}</span>
                        {!model.enabled && <span className="shrink-0 text-xs text-subtle-foreground">已停用</span>}
                      </span>
                      <span className="block truncate font-mono text-xs text-muted-foreground">{model.apiModelName}</span>
                    </span>
                    <span className="shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                      {scaledSummary(source.price, effectiveMultiplier(provider, model))}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <AlertDialog open={confirmRestore} onOpenChange={setConfirmRestore}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{restoreLabel}？</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody>
            <AlertDialogDescription>
              {status === "user-created-official"
                ? "你新建的条目会被丢弃，改用官方数据，并打开官方同步。"
                : "你对这个条目的修改会被丢弃，改回官方数据，并重新打开官方同步。"}
              {refs.length > 0 && ` ${refs.length} 个供应商模型此后按官方价格计价。`}
            </AlertDialogDescription>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                unit.discard();
                setEntrySync(entry.id, true);
                setConfirmRestore(false);
              }}
            >
              丢弃我的修改
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DetailPane>
  );
}

function Notice({ children, tone, action }: { children: React.ReactNode; tone?: "warn"; action?: React.ReactNode }) {
  return (
    <div className={cn("flex items-start gap-3 rounded-lg border px-4 py-3 text-sm", tone === "warn" ? "border-warn/40 bg-warn/5" : "border-border bg-muted/30")}>
      <Info className={cn("mt-0.5 size-4 shrink-0", tone === "warn" ? "text-warn" : "text-muted-foreground")} />
      <p className="min-w-0 flex-1">{children}</p>
      {action}
    </div>
  );
}

function DiffToggle({ open, onToggle, count, label = "查看差异" }: { open: boolean; onToggle: () => void; count: number; label?: string }) {
  return (
    <Button variant="ghost" size="sm" className="shrink-0" onClick={onToggle} aria-expanded={open}>
      {open ? <ChevronDown data-icon="inline-start" /> : <ChevronRight data-icon="inline-start" />}
      {label}
      <span className="tabular-nums text-muted-foreground">{count}</span>
    </Button>
  );
}

function DiffTable({
  diffs,
  onAdopt,
  leftLabel = "你的值",
  rightLabel = "官方值",
}: {
  diffs: ReturnType<typeof diffEntries>;
  onAdopt?: (field: string) => void;
  leftLabel?: string;
  rightLabel?: string;
}) {
  if (!diffs.length) return <p className="-mt-4 px-1 text-sm text-muted-foreground">草稿与官方值一致。</p>;
  return (
    <div className="-mt-4 overflow-hidden rounded-lg border border-border">
      <table className="w-full table-fixed text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            <th className="w-32 px-3 py-2 text-left font-normal">字段</th>
            <th className="px-3 py-2 text-left font-normal">{leftLabel}</th>
            <th className="px-3 py-2 text-left font-normal">{rightLabel}</th>
            {onAdopt && <th className="w-28 px-3 py-2" />}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {diffs.map((d) => (
            <tr key={d.field} className="align-top">
              <td className="px-3 py-2 text-muted-foreground">{d.label}</td>
              <td className="px-3 py-2 break-words tabular-nums">{d.before}</td>
              <td className="px-3 py-2 break-words tabular-nums text-foreground">{d.after}</td>
              {onAdopt && (
                <td className="px-3 py-1.5 text-right">
                  <Button variant="ghost" size="xs" onClick={() => onAdopt(d.field)}>
                    采用官方值
                  </Button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
