// PROTOTYPE — 项目设置页布局（#2971）的共用小件：分区框、风格字段、拦截对话框、生效摘要。
// 只是内容块，不含布局；三个变体各自决定怎么摆。

import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ImageOff, Loader2, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { STYLE_TEMPLATES } from "@/data/style-templates";

// ---------------------------------------------------------------------------
// 分区数据：页面把表单拆成「组 → 分区」，变体只拿这份数据决定摆法
// ---------------------------------------------------------------------------

export interface PsItem {
  id: string;
  title: string;
  description?: string;
  node: ReactNode;
}

export interface PsGroup {
  id: string;
  label: string;
  /** 记忆与 Agent 配置：自带保存方式，不归外壳保存栏管 */
  agent?: boolean;
  dirty?: boolean;
  /** 本组覆盖全局的项数，只有「模型」组有 */
  overrides?: number;
  items: PsItem[];
}

export interface PsSummaryRow {
  label: string;
  value: string;
  source: "project" | "global" | "locked";
  groupId: string;
}

// ---------------------------------------------------------------------------
// 分区框：不再用卡片，标题 + 说明 + 内容，分区之间一条细线
// ---------------------------------------------------------------------------

export function FormSection({ item, level = 2 }: { item: PsItem; level?: 2 | 3 }) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <section
      id={`ps-${item.id}`}
      className="relative scroll-mt-6 border-t border-border pt-6 first:border-t-0 first:pt-0"
    >
      <H className={cn("font-medium text-text", level === 2 ? "text-[15px]" : "text-[14px]")}>{item.title}</H>
      {item.description && <p className="mt-1 max-w-[40em] text-[13px] leading-[1.6] text-text-3">{item.description}</p>}
      <div className="mt-4">{item.node}</div>
    </section>
  );
}

/** 分页视图的页头：组名 + 可选的覆盖说明条 */
export function GroupHeader({ group, onResetOverrides }: { group: PsGroup; onResetOverrides?: () => void }) {
  return (
    <div className="mb-6">
      <h2 className="text-[17px] font-medium text-text">{group.label}</h2>
      {group.overrides !== undefined && (
        <OverrideNotice count={group.overrides} onReset={onResetOverrides} />
      )}
    </div>
  );
}

export function OverrideNotice({ count, onReset }: { count: number; onReset?: () => void }) {
  return (
    <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[13px]">
      <span className="text-text-2">
        {count === 0 ? "全部跟随全局默认模型。" : `本项目覆盖了 ${count} 项全局默认，其余跟随全局。`}
      </span>
      {count > 0 && onReset && (
        <Button variant="ghost" size="sm" onClick={onReset}>
          <RotateCcw data-icon="inline-start" />
          全部恢复为全局默认
        </Button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 风格字段：页面上只放当前风格；「更换」打开 Dialog（A、C）或在页内展开网格（B）
// ---------------------------------------------------------------------------

function StyleCurrent({ value }: { value: StylePickerValue }) {
  const { t } = useTranslation("templates");
  const tpl = value.mode === "template" && value.templateId ? STYLE_TEMPLATES.find((x) => x.id === value.templateId) : null;
  const img = tpl?.thumbnail ?? (value.mode === "custom" ? value.uploadedPreview : null);
  const name = tpl ? t(`name.${tpl.id}`) : value.mode === "custom" && value.uploadedPreview ? "自定义参考图" : "未设置风格";
  const tagline = tpl ? t(`tagline.${tpl.id}`, "") : value.mode === "custom" && value.uploadedPreview ? "按上传的参考图提取风格" : "生成时不附加风格描述";
  return (
    <div className="flex min-w-0 items-center gap-3">
      <div className="flex h-[72px] w-[54px] shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted">
        {img ? <img src={img} alt="" className="h-full w-full object-cover" /> : <ImageOff aria-hidden className="h-4 w-4 text-text-3" />}
      </div>
      <div className="min-w-0">
        <div className="truncate text-[14px] text-text">{name}</div>
        {tagline && <div className="mt-0.5 line-clamp-2 text-[13px] text-text-3">{tagline}</div>}
      </div>
    </div>
  );
}

export function StyleField({
  value,
  onChange,
  mode,
  canClear,
  onClear,
}: {
  value: StylePickerValue;
  onChange: (next: StylePickerValue) => void;
  mode: "dialog" | "inline";
  canClear: boolean;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<StylePickerValue>(value);
  const draftIncomplete = draft.mode === "template" ? !draft.templateId : !draft.uploadedPreview;

  const actions = (
    <div className="flex shrink-0 items-center gap-1">
      {canClear && (
        <Button variant="ghost" size="sm" onClick={onClear}>
          清除风格
        </Button>
      )}
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          if (mode === "dialog") setDraft(value);
          setOpen((v) => (mode === "inline" ? !v : true));
        }}
      >
        {mode === "inline" && open ? "收起" : "更换"}
      </Button>
    </div>
  );

  if (mode === "inline") {
    return (
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className="flex items-center justify-between gap-4">
          <StyleCurrent value={value} />
          {actions}
        </div>
        <CollapsibleContent className="mt-4">
          <StylePicker value={value} onChange={onChange} />
        </CollapsibleContent>
      </Collapsible>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between gap-4">
        <StyleCurrent value={value} />
        {actions}
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[min(720px,calc(100dvh-4rem))] flex-col gap-0 p-0 sm:max-w-[880px]">
          <DialogHeader className="shrink-0 border-b border-border px-5 py-4">
            <DialogTitle>选择风格</DialogTitle>
            <DialogDescription>选中后回到设置页，随其他修改一起保存。</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <StylePicker value={draft} onChange={setDraft} />
          </div>
          <DialogFooter className="mx-0 mb-0 shrink-0 px-5 py-3">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button
              disabled={draftIncomplete}
              onClick={() => {
                onChange(draft);
                setOpen(false);
              }}
            >
              使用此风格
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// 拦截对话框（「保存方式」第 8 条）：继续编辑 / 放弃修改 / 保存并离开
// ---------------------------------------------------------------------------

export function UnsavedGuardDialog({
  open,
  onStay,
  onDiscard,
  onSave,
}: {
  open: boolean;
  onStay: () => void;
  onDiscard: () => void;
  onSave: () => Promise<boolean>;
}) {
  const [saving, setSaving] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onStay()}>
      <DialogContent showCloseButton={false} className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>有未保存的修改</DialogTitle>
          <DialogDescription>项目设置的修改还没保存。离开前要怎么处理？</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onStay}>
            继续编辑
          </Button>
          <Button variant="outline" onClick={onDiscard}>
            放弃修改
          </Button>
          <Button
            disabled={saving}
            onClick={() => {
              setSaving(true);
              void onSave().finally(() => setSaving(false));
            }}
          >
            {saving && <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" />}
            保存并离开
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 生效配置摘要（B）：逐项列出生效值与来源，点击跳到对应分区
// ---------------------------------------------------------------------------

const SOURCE_LABEL: Record<PsSummaryRow["source"], string> = {
  project: "本项目",
  global: "全局默认",
  locked: "创建时锁定",
};

export function SummaryPanel({ rows, onJump }: { rows: PsSummaryRow[]; onJump: (groupId: string) => void }) {
  return (
    <div data-ps-summary className="rounded-lg border border-border bg-card/60 p-3">
      <div className="px-1 pb-2 text-[12px] text-text-3">生效配置</div>
      <dl className="space-y-px">
        {rows.map((row) => (
          <button
            key={row.label}
            type="button"
            onClick={() => onJump(row.groupId)}
            className="block w-full rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <dt className="flex items-center justify-between gap-2 text-[12px] text-text-3">
              <span>{row.label}</span>
              <span
                className={cn(
                  "shrink-0 rounded px-1 text-[11px]",
                  row.source === "project" ? "bg-primary/15 text-primary" : "text-text-3",
                )}
              >
                {SOURCE_LABEL[row.source]}
              </span>
            </dt>
            <dd className="mt-0.5 truncate text-[13px] text-text" title={row.value}>
              {row.value}
            </dd>
          </button>
        ))}
      </dl>
    </div>
  );
}
