// PROTOTYPE — #3091 变体 C 右侧：条目详情单页。
// 自上而下：页头（官方同步两段开关、对比官方值）→ 渠道表 → 模型事实 → 价目表；条目的修改走底部保存栏。
import { useCallback, useState } from "react";
import { ArrowUpCircle, Pencil } from "lucide-react";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "cn";
import {
  CAPABILITY_LABEL,
  MEDIA_LABEL,
  diffEntries,
  effectiveOf,
  makerLabel,
  officialOf,
  statusOf,
  type Capability,
  type CatalogEntry,
  type EntryData,
} from "../model";
import { useProto } from "../store";
import { ChannelsSection } from "./Channels";
import { PriceDialog, PriceTableView } from "./PriceEditor";
import { SectionHeading } from "./shared";

const CAPS_BY_MEDIA: Record<EntryData["mediaType"], Capability[]> = {
  text: ["text_generation", "structured_output", "vision"],
  image: ["text_to_image", "image_to_image"],
  video: [],
  audio: ["text_to_speech"],
};

export function EntryDetail({ entry }: { entry: CatalogEntry }) {
  const saveEntry = useProto((s) => s.saveEntry);
  const setEntrySync = useProto((s) => s.setEntrySync);
  const source = effectiveOf(entry);
  const official = officialOf(entry);
  const status = statusOf(entry);
  const userOwned = !entry.syncOn;

  const save = useCallback(
    async (value: EntryData) => {
      saveEntry(entry.id, value);
    },
    [saveEntry, entry.id],
  );
  const unit = useEditUnit<EntryData>({ source, save });
  const data = unit.value;

  // 同步开着时，编辑前先确认；确认后本页可编辑，保存即关闭同步
  const [unlocked, setUnlocked] = useState(false);
  const editable = userOwned || unlocked;
  const [confirmEdit, setConfirmEdit] = useState<null | "facts" | "price">(null);
  const [priceOpen, setPriceOpen] = useState(false);
  const [confirmFollow, setConfirmFollow] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);

  const requestEdit = (what: "facts" | "price") => {
    if (editable) {
      if (what === "price") setPriceOpen(true);
      return;
    }
    setConfirmEdit(what);
  };

  const outdated = status === "modified-outdated" || status === "user-created-official";
  const diffs = official && entry.user ? diffEntries(entry.user, official) : [];

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex min-w-0 items-center gap-2">
              <h2 className="truncate text-lg font-medium">{data.displayName}</h2>
              {data.deprecated && <Badge variant="outline">官方已下架</Badge>}
            </div>
            <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">
              <span className="font-mono text-xs">{entry.id}</span>
              <span>{makerLabel(data.maker)}</span>
              <span>{MEDIA_LABEL[data.mediaType]}</span>
              <span>
                {status === "synced" && `跟随官方，数据 ${official?.version}`}
                {status === "factory" && `跟随官方，出厂快照 ${official?.version}`}
                {(status === "modified" || status === "modified-outdated") && `你的版本，基于官方 ${entry.user?.version}`}
                {(status === "user-created" || status === "user-created-official") && "你新建的条目"}
              </span>
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <ToggleGroup
              aria-label="官方同步"
              variant="outline"
              size="sm"
              spacing={0}
              value={[entry.syncOn ? "official" : "mine"]}
              onValueChange={(next: string[]) => {
                if (!next[0]) return;
                if (next[0] === "official" && !entry.syncOn) {
                  if (!official) return;
                  setConfirmFollow(true);
                }
                if (next[0] === "mine" && entry.syncOn) setEntrySync(entry.id, false);
              }}
            >
              <ToggleGroupItem value="official" className="aria-pressed:bg-primary/15 aria-pressed:text-primary" disabled={!official} title={official ? undefined : "官方没有收录这个模型"}>
                跟随官方
              </ToggleGroupItem>
              <ToggleGroupItem value="mine" className="aria-pressed:bg-warn/15 aria-pressed:text-warn">
                我的版本
              </ToggleGroupItem>
            </ToggleGroup>
            {outdated && official && (
              <Button variant="link" size="xs" className="h-auto px-0" onClick={() => setCompareOpen(true)}>
                <ArrowUpCircle aria-hidden />
                {status === "user-created-official" ? "官方已收录，对比官方值" : "官方有更新，对比官方值"}
              </Button>
            )}
          </div>
        </div>
      }
      footer={
        editable ? (
          <div className="flex max-w-232 flex-col gap-1.5">
            {entry.syncOn && unit.dirty && <p className="text-xs text-warn">保存后这个条目改由你负责，不再接收官方同步。</p>}
            <SaveBar unit={unit} />
          </div>
        ) : undefined
      }
    >
      <div className="flex max-w-244 flex-col gap-10 px-6 py-6">
        <ChannelsSection entryId={entry.id} data={source} />

        <section className="flex flex-col gap-3">
          <SectionHeading
            title="模型事实"
            hint="模型本身的能力，各渠道共用。渠道的限制写在渠道的能力覆盖里，不要改这里。"
            action={
              !editable && (
                <Button variant="outline" size="sm" onClick={() => requestEdit("facts")}>
                  <Pencil aria-hidden />
                  编辑
                </Button>
              )
            }
          />
          <Facts data={data} editable={editable} onChange={(next) => unit.setValue(next)} />
        </section>

        <section className="flex flex-col gap-3">
          <SectionHeading
            title="价目表"
            hint="官方价格。各渠道的价差用倍率表达，不在这里改。"
            action={
              <Button variant="outline" size="sm" onClick={() => requestEdit("price")}>
                <Pencil aria-hidden />
                编辑价格
              </Button>
            }
          />
          <PriceTableView price={data.price} />
        </section>
      </div>

      <PriceDialog open={priceOpen} onOpenChange={setPriceOpen} value={data.price} title={data.displayName} onApply={(price) => unit.setValue((d) => ({ ...d, price }))} />

      <AlertDialog open={confirmEdit !== null} onOpenChange={(v: boolean) => !v && setConfirmEdit(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>编辑会关闭这个条目的官方同步</AlertDialogTitle>
            <AlertDialogDescription>
              保存后，{data.displayName} 改由你负责，之后的官方价格与能力更新都不会再应用到它。随时可以切回「跟随官方」，丢弃你的修改。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setUnlocked(true);
                if (confirmEdit === "price") setPriceOpen(true);
                setConfirmEdit(null);
              }}
            >
              继续编辑
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmFollow} onOpenChange={setConfirmFollow}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>丢弃你的版本，改为跟随官方？</AlertDialogTitle>
            <AlertDialogDescription>
              {diffs.length > 0 ? `你的版本与官方最新数据有 ${diffs.length} 处不同，丢弃后不能恢复。` : "你的版本与官方最新数据相同。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>保留我的版本</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setEntrySync(entry.id, true);
                setConfirmFollow(false);
              }}
            >
              丢弃并跟随官方
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {official && entry.user && (
        <CompareDialog
          open={compareOpen}
          onOpenChange={setCompareOpen}
          mine={entry.user}
          official={official}
          onUseOfficial={() => {
            setEntrySync(entry.id, true);
            setCompareOpen(false);
          }}
        />
      )}
    </DetailPane>
  );
}

function Facts({ data, editable, onChange }: { data: EntryData; editable: boolean; onChange: (d: EntryData) => void }) {
  const caps = CAPS_BY_MEDIA[data.mediaType];
  const rows: { label: string; view: React.ReactNode; edit: React.ReactNode }[] = [];
  if (caps.length) {
    rows.push({
      label: "能力",
      view: caps.filter((c) => data.capabilities.includes(c)).map((c) => CAPABILITY_LABEL[c]).join("、") || "无",
      edit: (
        <div className="flex flex-wrap gap-4">
          {caps.map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={data.capabilities.includes(c)}
                onCheckedChange={(v: boolean) => onChange({ ...data, capabilities: v ? [...data.capabilities, c] : data.capabilities.filter((x) => x !== c) })}
              />
              {CAPABILITY_LABEL[c]}
            </label>
          ))}
        </div>
      ),
    });
  }
  if (data.mediaType === "text") {
    rows.push({
      label: "最大输出",
      view: data.maxOutputTokens ? `${data.maxOutputTokens.toLocaleString()} token` : "未登记",
      edit: <Input type="number" className="w-36" value={data.maxOutputTokens ?? ""} onChange={(e) => onChange({ ...data, maxOutputTokens: e.target.value ? Number(e.target.value) : null })} />,
    });
  }
  if (data.mediaType === "video") {
    rows.push({
      label: "可选时长",
      view: data.durations.length ? `${data.durations.join("、")} 秒` : "未登记",
      edit: (
        <Input
          key={data.durations.join()}
          className="w-full max-w-md"
          placeholder="如 5、10"
          defaultValue={data.durations.join("、")}
          onBlur={(e) => onChange({ ...data, durations: e.target.value.split(/[,，、\s]+/).filter(Boolean).map(Number).filter((n) => n > 0) })}
        />
      ),
    });
  }
  if (data.mediaType === "video" || data.mediaType === "image") {
    rows.push({
      label: "分辨率",
      view: data.resolutions.join("、") || "未登记",
      edit: <Input key={data.resolutions.join()} className="w-full max-w-md" defaultValue={data.resolutions.join("、")} onBlur={(e) => onChange({ ...data, resolutions: e.target.value.split(/[,，、\s]+/).filter(Boolean) })} />,
    });
  }
  const constraints = Object.entries(data.durationConstraints);
  if (constraints.length) {
    rows.push({ label: "联动约束", view: constraints.map(([res, ds]) => `${res} 仅 ${ds.join("、")} 秒`).join("；"), edit: <span className="text-sm text-muted-foreground">{constraints.map(([res, ds]) => `${res} 仅 ${ds.join("、")} 秒`).join("；")}</span> });
  }
  if (data.mediaType === "video") {
    rows.push({ label: "输入模式等", view: <span className="text-muted-foreground">由调用通道声明，见各渠道的能力覆盖</span>, edit: <span className="text-sm text-muted-foreground">由调用通道声明，见各渠道的能力覆盖</span> });
  }

  return (
    <dl className="grid grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-3 rounded-lg border border-border px-4 py-3.5 text-sm">
      {rows.map((r) => (
        <div key={r.label} className="contents">
          <dt className="text-subtle-foreground">{r.label}</dt>
          <dd className={cn("min-w-0", !editable && "truncate")}>{editable ? r.edit : r.view}</dd>
        </div>
      ))}
    </dl>
  );
}

function CompareDialog({ open, onOpenChange, mine, official, onUseOfficial }: { open: boolean; onOpenChange: (v: boolean) => void; mine: EntryData; official: EntryData; onUseOfficial: () => void }) {
  const diffs = diffEntries(mine, official);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>对比官方值 · {official.displayName}</DialogTitle>
          <DialogDescription>
            左边是你的版本（基于官方 {mine.version === "user" ? "—，你新建" : mine.version}），右边是官方最新（{official.version}）。只列出不同的项。
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {diffs.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">两边完全相同。</p>
          ) : (
            <div className="grid grid-cols-[9rem_1fr_1fr] overflow-hidden rounded-lg border border-border text-sm">
              <div className="border-b border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">项目</div>
              <div className="border-b border-l border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">我的版本</div>
              <div className="border-b border-l border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">官方最新</div>
              {diffs.map((d) => (
                <div key={d.field} className="contents">
                  <div className="border-b border-border px-3 py-2 text-subtle-foreground last:border-b-0">{d.label}</div>
                  <div className="border-b border-l border-border bg-warn/5 px-3 py-2 break-words tabular-nums">{d.before}</div>
                  <div className="border-b border-l border-border bg-primary/8 px-3 py-2 break-words tabular-nums">{d.after}</div>
                </div>
              ))}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            保留我的版本
          </Button>
          <Button onClick={onUseOfficial}>改用官方值</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
