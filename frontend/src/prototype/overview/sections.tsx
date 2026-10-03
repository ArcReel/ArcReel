// PROTOTYPE — 项目概览页原型（#2981）的各个区块，不合并。写操作全部是桩，只弹提示。

import { useEffect, useState, type ReactNode } from "react";
import { Link } from "wouter";
import { Film, Image as ImageIcon, Package, RefreshCw, Settings2, ShoppingBag, Upload, Users, Landmark, Clapperboard } from "lucide-react";
import type { EpisodeMeta, ProjectData } from "@/types";
import type { CostByType } from "@/types/cost";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useCostStore } from "@/stores/cost-store";
import { useAppStore } from "@/stores/app-store";
import { costEntries, formatCost, totalBreakdown } from "@/utils/cost-format";
import { normalizeRoute } from "@/utils/generation-mode";
import type { AxisState } from "./axes";

export function stub(text: string) {
  useAppStore.getState().pushToast(`原型：${text}（桩，未写入）`, "info");
}

export const MODE_LABEL: Record<ProjectData["content_mode"], string> = {
  drama: "剧情演绎",
  narration: "说书/旁白",
  ad: "广告/短片",
};

export function aspectLabel(p: ProjectData): string | null {
  const a = typeof p.aspect_ratio === "string" ? p.aspect_ratio : null;
  if (!a) return null;
  return a === "9:16" ? "竖屏 9:16" : a === "16:9" ? "横屏 16:9" : a;
}

export function formatDuration(sec: number | undefined): string | null {
  if (!sec) return null;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function itemNoun(p: ProjectData): string {
  return normalizeRoute(p.generation_mode) === "reference_video" ? "视频单元" : "分镜";
}

// ---------------------------------------------------------------------------
// 通用：区块外框与标题
// ---------------------------------------------------------------------------

export function Section({
  title,
  aside,
  children,
  className = "",
  bare = false,
}: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  /** 不画外框，只留标题与内容 */
  bare?: boolean;
}) {
  return (
    <section className={`${bare ? "" : "rounded-[10px] border border-border bg-card p-5"} ${className}`}>
      {(title || aside) && (
        <div className="mb-3 flex min-h-7 items-center gap-2">
          {title && <h2 className="text-[13px] font-medium text-[var(--color-text-2)]">{title}</h2>}
          <div className="flex-1" />
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

function UnsavedBar({ onDiscard, onSave }: { onDiscard: () => void; onSave: () => void }) {
  return (
    <div
      role="status"
      className="mt-4 flex items-center gap-2 rounded-lg border border-[var(--color-warm-ring,var(--color-hairline))] bg-[var(--color-warm-soft,transparent)] px-3 py-2 text-[13px]"
    >
      <span className="size-1.5 rounded-full bg-[var(--color-warn)]" aria-hidden />
      <span className="text-[var(--color-text-2)]">未保存的修改</span>
      <div className="flex-1" />
      <Button variant="ghost" size="sm" onClick={onDiscard}>
        放弃修改
      </Button>
      <Button size="sm" onClick={onSave}>
        保存
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 页头
// ---------------------------------------------------------------------------

export function OverviewHeader({ data, mode }: { data: ProjectData; mode: AxisState["header"] }) {
  if (mode === "none") return null;
  if (mode === "title") {
    return (
      <header>
        <h1 className="display-serif text-[28px] font-semibold tracking-tight text-foreground">{data.title}</h1>
        <p className="mt-0.5 text-[13px] text-muted-foreground">{MODE_LABEL[data.content_mode]}</p>
      </header>
    );
  }
  const eps = data.episodes?.length ?? 0;
  const total = (data.episodes ?? []).reduce((s, e) => s + (e.duration_seconds ?? 0), 0);
  const meta = [
    MODE_LABEL[data.content_mode],
    aspectLabel(data),
    data.style && data.style.length <= 16 ? data.style : null,
    data.content_mode === "ad" ? null : eps > 0 ? `${eps} 集` : "还没有集",
    total > 0 ? `已出片 ${formatDuration(total)}` : null,
  ].filter(Boolean);
  return (
    <header className="flex items-end gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="display-serif truncate text-[24px] font-semibold tracking-tight text-foreground">{data.title}</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">{meta.join(" · ")}</p>
      </div>
      <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/settings" />}>
        <Settings2 />
        项目设置
      </Button>
    </header>
  );
}

// ---------------------------------------------------------------------------
// 故事设定
// ---------------------------------------------------------------------------

type Draft = { synopsis: string; genre: string; theme: string; world_setting: string };
const EMPTY_DRAFT: Draft = { synopsis: "", genre: "", theme: "", world_setting: "" };

const FIELD_LABEL: Record<keyof Draft, string> = {
  synopsis: "梗概",
  genre: "类型",
  theme: "主题",
  world_setting: "世界观",
};

export function StorySetting({
  data,
  fields,
  analyzing,
  emptyProject,
  onUploadSource,
  bare,
  collapsedSummary = false,
}: {
  data: ProjectData;
  fields: AxisState["fields"];
  analyzing: boolean;
  /** 项目还没有任何集与概述：设定区承担上传原文的入口 */
  emptyProject: boolean;
  onUploadSource: () => void;
  bare?: boolean;
  /** 集优先版式：默认只显示两行梗概与标签，展开后才是完整设定 */
  collapsedSummary?: boolean;
}) {
  const saved: Draft = data.overview
    ? {
        synopsis: data.overview.synopsis ?? "",
        genre: data.overview.genre ?? "",
        theme: data.overview.theme ?? "",
        world_setting: data.overview.world_setting ?? "",
      }
    : EMPTY_DRAFT;
  const [base, setBase] = useState<Draft>(saved);
  const [draft, setDraft] = useState<Draft>(saved);
  const [editing, setEditing] = useState(false);
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [expanded, setExpanded] = useState(!collapsedSummary);
  const savedKey = JSON.stringify(saved);
  useEffect(() => {
    const next = JSON.parse(savedKey) as Draft;
    setBase(next);
    setDraft(next);
  }, [savedKey]);

  const dirty = (Object.keys(draft) as (keyof Draft)[]).some((k) => draft[k] !== base[k]);
  const save = () => {
    setBase(draft);
    setEditing(false);
    stub("保存故事设定");
  };
  const discard = () => {
    setDraft(base);
    setEditing(false);
  };

  const regenButton = data.overview && !analyzing && (
    <Button variant="ghost" size="sm" onClick={() => setConfirmRegen(true)}>
      <RefreshCw />
      {dirty ? "放弃修改并重新生成" : "从原文重新生成"}
    </Button>
  );

  let body: ReactNode;
  if (analyzing) {
    body = (
      <div role="status" aria-live="polite" className="space-y-3">
        <p className="flex items-center gap-2 text-[13px] text-[var(--color-text-2)]">
          <span className="size-1.5 animate-pulse rounded-full bg-primary" aria-hidden />
          正在读取原文，写出梗概、类型、主题与世界观…
        </p>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-[92%]" />
        <Skeleton className="h-4 w-[60%]" />
        <div className="flex gap-2 pt-1">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-6 w-28" />
        </div>
      </div>
    );
  } else if (emptyProject && !data.overview && !dirty && fields !== "editMode") {
    body = (
      <div className="space-y-4">
        <button
          type="button"
          onClick={onUploadSource}
          className="focus-ring flex w-full flex-col items-center gap-1.5 rounded-[10px] border border-dashed border-[var(--color-hairline-strong)] px-6 py-8 text-center transition-colors hover:border-primary/50 hover:bg-primary/5"
        >
          <Upload className="size-5 text-muted-foreground" />
          <span className="text-[14px] font-medium text-foreground">上传原文，AI 会读出故事设定</span>
          <span className="text-[13px] text-muted-foreground">拖入或点击选择 · TXT、MD、DOCX、EPUB、PDF</span>
        </button>
        <p className="text-[13px] text-muted-foreground">也可以不上传原文，直接在下面写：</p>
        <SettingFields draft={draft} setDraft={setDraft} fields={fields} />
      </div>
    );
  } else if (fields === "editMode" && !editing) {
    body = data.overview || dirty ? (
      <SettingRead draft={base} />
    ) : (
      <Button variant="outline" onClick={() => setEditing(true)}>
        填写故事设定
      </Button>
    );
  } else if (collapsedSummary && !expanded) {
    body = (
      <button type="button" onClick={() => setExpanded(true)} className="focus-ring block w-full rounded-md text-left">
        <p className="line-clamp-2 max-w-[60em] text-[14px] leading-[1.7] text-[var(--color-text-2)]">
          {base.synopsis || "还没有梗概"}
        </p>
        <p className="mt-2 text-[13px] text-muted-foreground">
          {[base.genre && `类型 · ${base.genre}`, base.theme && `主题 · ${base.theme}`].filter(Boolean).join("　")}
          <span className="ml-3 text-primary">展开设定</span>
        </p>
      </button>
    );
  } else {
    body = <SettingFields draft={draft} setDraft={setDraft} fields={fields} />;
  }

  return (
    <Section
      bare={bare}
      title="故事设定"
      aside={
        <>
          {fields === "editMode" && !editing && !analyzing && (data.overview || dirty) && (
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
              编辑
            </Button>
          )}
          {collapsedSummary && expanded && !analyzing && (
            <Button variant="ghost" size="sm" onClick={() => setExpanded(false)}>
              收起
            </Button>
          )}
          {regenButton}
        </>
      }
    >
      {body}
      {fields === "editMode" && editing ? (
        <div className="mt-4 flex gap-2">
          <Button size="sm" onClick={save}>
            保存
          </Button>
          <Button size="sm" variant="ghost" onClick={discard}>
            取消
          </Button>
        </div>
      ) : (
        dirty && <UnsavedBar onDiscard={discard} onSave={save} />
      )}
      <AlertDialog open={confirmRegen} onOpenChange={setConfirmRegen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>从原文重新生成故事设定？</AlertDialogTitle>
            <AlertDialogDescription>
              梗概、类型、主题与世界观会被整份替换
              {dirty ? "，你还没保存的修改也会丢失" : ""}。这一步不能撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setConfirmRegen(false);
                discard();
                stub("重新生成故事设定");
              }}
            >
              重新生成
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  );
}

function SettingRead({ draft }: { draft: Draft }) {
  return (
    <div className="space-y-3">
      {draft.synopsis && <p className="max-w-[40em] text-[14px] leading-[1.75] text-[var(--color-text-2)]">{draft.synopsis}</p>}
      <div className="flex flex-wrap gap-2 text-[13px]">
        {draft.genre && <Tag label="类型" value={draft.genre} />}
        {draft.theme && <Tag label="主题" value={draft.theme} />}
      </div>
      {draft.world_setting && (
        <div>
          <div className="text-[13px] text-muted-foreground">世界观</div>
          <p className="mt-1 max-w-[40em] text-[14px] leading-[1.75] text-[var(--color-text-2)]">{draft.world_setting}</p>
        </div>
      )}
    </div>
  );
}

function Tag({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary/60 px-2 py-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-[var(--color-text-2)]">{value}</span>
    </span>
  );
}

function SettingFields({
  draft,
  setDraft,
  fields,
}: {
  draft: Draft;
  setDraft: (fn: (d: Draft) => Draft) => void;
  fields: AxisState["fields"];
}) {
  const seamless = fields === "seamless";
  const area = seamless
    ? "min-h-0 resize-none border-transparent bg-transparent px-2 -mx-2 text-[14px] leading-[1.75] text-[var(--color-text-2)] shadow-none hover:border-border focus-visible:border-ring dark:bg-transparent md:text-[14px]"
    : "resize-none text-[14px] leading-[1.7] md:text-[14px]";
  const input = seamless
    ? "h-8 border-transparent bg-transparent px-2 -mx-2 text-[14px] shadow-none hover:border-border dark:bg-transparent md:text-[14px]"
    : "h-8 text-[14px] md:text-[14px]";
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  const label = "text-[13px] text-muted-foreground";
  return (
    <div className="space-y-3">
      <label className="block max-w-[40em]">
        <span className={label}>{FIELD_LABEL.synopsis}</span>
        <Textarea value={draft.synopsis} onChange={set("synopsis")} placeholder="一两段话讲清楚这个故事" className={`mt-1 max-h-[40cqh] ${area}`} />
      </label>
      <div className="grid max-w-[40em] grid-cols-2 gap-3">
        <label className="block">
          <span className={label}>{FIELD_LABEL.genre}</span>
          <Input value={draft.genre} onChange={set("genre")} placeholder="如：科幻 / 悬疑" className={`mt-1 ${input}`} />
        </label>
        <label className="block">
          <span className={label}>{FIELD_LABEL.theme}</span>
          <Input value={draft.theme} onChange={set("theme")} placeholder="如：文明与生存" className={`mt-1 ${input}`} />
        </label>
      </div>
      <label className="block max-w-[40em]">
        <span className={label}>{FIELD_LABEL.world_setting}</span>
        <Textarea value={draft.world_setting} onChange={set("world_setting")} placeholder="时代、地点、规则" className={`mt-1 ${area}`} />
      </label>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 广告：创作灵感与商品
// ---------------------------------------------------------------------------

const AD_TIERS = ["15", "30", "60", "90"];

function DurationTiers({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const isTier = AD_TIERS.includes(value);
  const [custom, setCustom] = useState(!isTier);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ToggleGroup
        variant="outline"
        size="sm"
        value={[custom ? "custom" : value]}
        onValueChange={(v: string[]) => {
          const next = v[0];
          if (!next) return;
          if (next === "custom") setCustom(true);
          else {
            setCustom(false);
            onChange(next);
          }
        }}
      >
        {AD_TIERS.map((t) => (
          <ToggleGroupItem key={t} value={t} className="px-3 text-[13px] tabular-nums">
            {t} 秒
          </ToggleGroupItem>
        ))}
        <ToggleGroupItem value="custom" className="px-3 text-[13px]">
          自定义
        </ToggleGroupItem>
      </ToggleGroup>
      {custom && (
        <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground">
          <Input type="number" value={value} onChange={(e) => onChange(e.target.value)} className="h-7 w-20 text-[13px] md:text-[13px]" />秒
        </span>
      )}
    </div>
  );
}

export function AdBrief({ data, bare, duration = "number" }: { data: ProjectData; bare?: boolean; duration?: AxisState["adDuration"] }) {
  const [base, setBase] = useState({ brief: data.brief ?? "", target: String(data.target_duration ?? "") });
  const [draft, setDraft] = useState(base);
  const dirty = draft.brief !== base.brief || draft.target !== base.target;
  return (
    <Section bare={bare} title="创作灵感">
      <div className="max-w-[40em] space-y-3">
        <Textarea
          value={draft.brief}
          onChange={(e) => setDraft((d) => ({ ...d, brief: e.target.value }))}
          placeholder="想拍什么、给谁看、什么调性"
          className="resize-none text-[14px] leading-[1.7] md:text-[14px]"
        />
        {duration === "tiers" ? (
          <div className="flex items-center gap-3 text-[13px] text-muted-foreground">
            目标时长
            <DurationTiers value={draft.target} onChange={(v) => setDraft((d) => ({ ...d, target: v }))} />
          </div>
        ) : (
        <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
          目标时长
          <Input
            type="number"
            value={draft.target}
            onChange={(e) => setDraft((d) => ({ ...d, target: e.target.value }))}
            className="h-8 w-20 text-[14px] md:text-[14px]"
          />
          秒
        </label>
        )}
      </div>
      {dirty && (
        <UnsavedBar
          onDiscard={() => setDraft(base)}
          onSave={() => {
            setBase(draft);
            stub("保存创作灵感");
          }}
        />
      )}
    </Section>
  );
}

export function AdProducts({ data, bare }: { data: ProjectData; bare?: boolean }) {
  const products = Object.entries(data.products ?? {});
  return (
    <Section
      bare={bare}
      title="商品"
      aside={
        products.length > 0 && (
          <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/products" />}>
            查看全部
          </Button>
        )
      }
    >
      {products.length === 0 ? (
        <Empty className="border border-dashed border-border py-8">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ShoppingBag />
            </EmptyMedia>
            <EmptyTitle>还没有商品</EmptyTitle>
            <EmptyDescription>添加商品图和卖点，AI 写脚本时会围绕它来拍。没有商品也可以只凭创作灵感生成。</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button variant="outline" onClick={() => stub("打开商品详情 Sheet，新建商品")}>
              添加商品
            </Button>
          </EmptyContent>
        </Empty>
      ) : (
        <ul className="space-y-1">
          {products.map(([name, p]) => (
            <li key={name} className="flex items-center gap-3 rounded-md px-1 py-1.5 text-[14px]">
              <Package className="size-4 text-muted-foreground" />
              <span className="text-foreground">{name}</span>
              <span className="truncate text-[13px] text-muted-foreground">{(p as { description?: string }).description}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 资产完成度
// ---------------------------------------------------------------------------

const ASSET_KINDS = [
  { key: "character", label: "角色", path: "/characters", icon: Users },
  { key: "scene", label: "场景", path: "/scenes", icon: Landmark },
  { key: "prop", label: "道具", path: "/props", icon: Package },
  { key: "product", label: "商品", path: "/products", icon: ShoppingBag },
] as const;

function assetRows(data: ProjectData) {
  const isAd = data.content_mode === "ad";
  return ASSET_KINDS.filter((k) => (k.key === "product" ? isAd : true)).map((k) => {
    const c = data.status?.assets?.[k.key] ?? { total: 0, available: 0, stale: 0 };
    return { ...k, ...c };
  });
}

export function AssetsBlock({ data, mode, bare }: { data: ProjectData; mode: AxisState["assets"]; bare?: boolean }) {
  if (mode === "none") return null;
  const rows = assetRows(data);
  if (mode === "inline") {
    return (
      <div className="flex flex-wrap items-center gap-x-1 gap-y-1 text-[13px]">
        <span className="mr-2 text-muted-foreground">资产图</span>
        {rows.map((r, i) => (
          <span key={r.key} className="inline-flex items-center">
            {i > 0 && <span className="mx-1.5 text-[var(--color-hairline-strong)]">·</span>}
            <Link href={r.path} className="focus-ring rounded px-1 text-[var(--color-text-2)] hover:text-foreground hover:underline">
              {r.label} <span className="tabular-nums">{r.available}/{r.total}</span>
              {r.stale > 0 && <span className="ml-1 text-[var(--color-warn)]">· {r.stale} 过期</span>}
            </Link>
          </span>
        ))}
      </div>
    );
  }
  return (
    <Section bare={bare} title="资产图">
      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
        {rows.map((r) => {
          const pct = r.total > 0 ? Math.round((r.available / r.total) * 100) : 0;
          const Icon = r.icon;
          return (
            <Link
              key={r.key}
              href={r.path}
              className="focus-ring block rounded-lg border border-border px-3 py-2.5 transition-colors hover:bg-secondary/40"
            >
              <div className="flex items-center gap-2 text-[13px]">
                <Icon className="size-3.5 text-muted-foreground" />
                <span className="text-[var(--color-text-2)]">{r.label}</span>
                <span className="ml-auto tabular-nums text-muted-foreground">
                  {r.available}/{r.total}
                </span>
              </div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-secondary">
                <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
              </div>
            </Link>
          );
        })}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 费用
// ---------------------------------------------------------------------------

function costRows(t: CostByType, actual: boolean) {
  const rows: [string, string][] = [
    ["分镜图", formatCost(t.image)],
    ["视频", formatCost(t.video)],
  ];
  if (costEntries(t.audio).length > 0) rows.push(["配音", formatCost(t.audio)]);
  if (actual) {
    for (const [k, l] of [
      ["characters", "角色图"],
      ["scenes", "场景图"],
      ["props", "道具图"],
      ["products", "商品图"],
    ] as const) {
      if (t[k] != null) rows.push([l, formatCost(t[k])]);
    }
    if (costEntries(t.unassigned).length > 0) rows.push(["历史未归属", formatCost(t.unassigned)]);
  }
  return rows;
}

function CostColumn({ label, t, actual }: { label: string; t: CostByType; actual: boolean }) {
  return (
    <div>
      <div className="mb-1.5 text-[13px] text-muted-foreground">{label}</div>
      <dl className="space-y-1 text-[13px] tabular-nums">
        {costRows(t, actual).map(([l, v]) => (
          <div key={l} className="flex gap-2">
            <dt className="text-muted-foreground">{l}</dt>
            <dd className="flex-1 text-right text-[var(--color-text-2)]">{v}</dd>
          </div>
        ))}
        <div className="mt-2 flex gap-2 border-t border-border pt-2">
          <dt className="text-muted-foreground">合计</dt>
          <dd className="flex-1 text-right text-[14px] font-semibold text-foreground">{formatCost(totalBreakdown(t))}</dd>
        </div>
      </dl>
    </div>
  );
}

export function CostBlock({ mode, bare }: { mode: AxisState["cost"]; bare?: boolean }) {
  const totals = useCostStore((s) => s.costData?.project_totals);
  const loading = useCostStore((s) => s.loading);
  const usageLink = (
    <Link href="~/app/settings?section=usage" className="focus-ring rounded text-[13px] text-primary hover:underline">
      使用记录
    </Link>
  );
  if (mode === "none") {
    return <div className="text-[13px] text-muted-foreground">费用明细见 {usageLink}</div>;
  }
  if (!totals) {
    return <div className="text-[13px] text-muted-foreground">{loading ? "正在计算费用…" : "暂无费用数据"}</div>;
  }
  if (mode === "summary") {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
        <span className="text-muted-foreground">费用</span>
        <span className="tabular-nums text-[var(--color-text-2)]">
          预估 {formatCost(totalBreakdown(totals.estimate))}
          <span className="mx-1.5 text-[var(--color-hairline-strong)]">·</span>
          已花 {formatCost(totalBreakdown(totals.actual))}
        </span>
        <Popover>
          <PopoverTrigger className="focus-ring rounded text-[13px] text-primary hover:underline">明细</PopoverTrigger>
          <PopoverContent align="start" className="w-[420px]">
            <div className="grid grid-cols-2 gap-5">
              <CostColumn label="预估（当前脚本）" t={totals.estimate} actual={false} />
              <CostColumn label="实际" t={totals.actual} actual />
            </div>
            <div className="mt-3 border-t border-border pt-2 text-right">{usageLink}</div>
          </PopoverContent>
        </Popover>
      </div>
    );
  }
  return (
    <Section bare={bare} title="费用" aside={usageLink}>
      <div className="grid grid-cols-1 gap-5 @[520px]:grid-cols-2">
        <CostColumn label="预估（当前脚本）" t={totals.estimate} actual={false} />
        <CostColumn label="实际" t={totals.actual} actual />
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// 集清单
// ---------------------------------------------------------------------------

const STATUS_LABEL: Record<NonNullable<EpisodeMeta["status"]>, string> = {
  draft: "待规划",
  scripted: "已有脚本",
  in_production: "制作中",
  completed: "已完成",
  missing: "脚本缺失",
};

function MiniBar({ label, c, icon: Icon }: { label: string; c?: { total: number; available: number; stale: number }; icon: typeof Film }) {
  const total = c?.total ?? 0;
  const avail = c?.available ?? 0;
  const pct = total > 0 ? (avail / total) * 100 : 0;
  return (
    <div className="flex items-center gap-2 text-[13px]">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="w-12 shrink-0 text-muted-foreground">{label}</span>
      <div className="h-1 min-w-8 flex-1 overflow-hidden rounded-full bg-secondary">
        <div className={`h-full rounded-full ${pct >= 100 ? "bg-[var(--color-good)]" : "bg-primary"}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 shrink-0 text-right tabular-nums text-[var(--color-text-2)]">
        {avail}/{total}
      </span>
    </div>
  );
}

function epName(data: ProjectData, ep: EpisodeMeta, index: number) {
  if (data.content_mode === "ad") return ep.title || data.title;
  return ep.title || `第 ${index + 1} 集`;
}

export function EpisodesBlock({
  data,
  mode,
  bare,
}: {
  data: ProjectData;
  mode: AxisState["episodeList"];
  bare?: boolean;
}) {
  const getEpisodeCost = useCostStore((s) => s.getEpisodeCost);
  if (mode === "none") return null;
  const eps = data.episodes ?? [];
  const isAd = data.content_mode === "ad";
  const noun = itemNoun(data);
  const title = isAd ? "短片" : "集";

  if (eps.length === 0) {
    return (
      <Section bare={bare} title={title}>
        <p className="text-[13px] text-muted-foreground">
          还没有集。上传原文后用「分集」做 AI 规划，或者新建一集从空白开始。
          <Link href="/episodes" className="ml-1 text-primary hover:underline">
            去分集
          </Link>
        </p>
      </Section>
    );
  }

  if (mode === "cards") {
    return (
      <Section
        bare
        title={isAd ? "短片" : `全部 ${eps.length} 集`}
        aside={
          !isAd && (
            <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/episodes" />}>
              管理分集
            </Button>
          )
        }
      >
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
          {eps.map((ep, i) => (
            <Link
              key={ep.episode}
              href={`/episodes/${ep.episode}`}
              className="focus-ring flex flex-col gap-3 rounded-[10px] border border-border bg-card p-4 transition-colors hover:border-[var(--color-hairline-strong)] hover:bg-secondary/30"
            >
              <div className="flex items-baseline gap-2">
                {!isAd && <span className="text-[13px] tabular-nums text-muted-foreground">{String(i + 1).padStart(2, "0")}</span>}
                <span className="line-clamp-2 min-w-0 flex-1 text-[14px] font-medium text-foreground">{epName(data, ep, i)}</span>
              </div>
              <div className="space-y-1.5">
                <MiniBar label="分镜图" c={ep.storyboards} icon={ImageIcon} />
                <MiniBar label="视频" c={ep.videos} icon={Film} />
              </div>
              <div className="mt-auto flex items-center gap-2 text-[13px] text-muted-foreground">
                <span>{STATUS_LABEL[ep.status ?? "draft"]}</span>
                {ep.item_count != null && <span>· {ep.item_count} 个{noun}</span>}
                {formatDuration(ep.duration_seconds) && <span className="ml-auto tabular-nums">{formatDuration(ep.duration_seconds)}</span>}
              </div>
            </Link>
          ))}
        </div>
      </Section>
    );
  }

  return (
    <Section
      bare={bare}
      title={isAd ? "短片" : `集 · ${eps.length}`}
      aside={
        !isAd && (
          <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/episodes" />}>
            管理分集
          </Button>
        )
      }
    >
      <ul className="-mx-2">
        {eps.map((ep, i) => {
          const cost = mode === "costRows" ? getEpisodeCost(ep.episode) : undefined;
          const sb = ep.storyboards;
          const vd = ep.videos;
          return (
            <li key={ep.episode}>
              <Link
                href={`/episodes/${ep.episode}`}
                className="focus-ring flex items-center gap-3 rounded-md px-2 py-2 text-[13px] transition-colors hover:bg-secondary/40"
              >
                {!isAd && <span className="w-6 shrink-0 tabular-nums text-muted-foreground">{String(i + 1).padStart(2, "0")}</span>}
                <span className="min-w-0 flex-1 truncate text-[14px] text-foreground">{epName(data, ep, i)}</span>
                <span className="hidden w-28 shrink-0 @[480px]:block">
                  <span className="flex h-1 overflow-hidden rounded-full bg-secondary" title={`分镜图 ${sb?.available ?? 0}/${sb?.total ?? 0} · 视频 ${vd?.available ?? 0}/${vd?.total ?? 0}`}>
                    <span className="h-full bg-primary/50" style={{ width: `${sb?.total ? ((sb.available - (vd?.available ?? 0)) / sb.total) * 100 : 0}%` }} />
                    <span className="h-full bg-primary" style={{ width: `${vd?.total ? (vd.available / vd.total) * 100 : 0}%` }} />
                  </span>
                </span>
                <span className="w-16 shrink-0 text-right text-muted-foreground">{STATUS_LABEL[ep.status ?? "draft"]}</span>
                <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">{formatDuration(ep.duration_seconds) ?? "—"}</span>
                {cost && (
                  <span className="w-44 shrink-0 text-right tabular-nums text-muted-foreground">
                    预估 {formatCost(totalBreakdown(cost.totals.estimate))} · 实 {formatCost(totalBreakdown(cost.totals.actual))}
                  </span>
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export function NotFoundCanvas() {
  return (
    <div className="grid h-full place-items-center p-8">
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Clapperboard />
          </EmptyMedia>
          <EmptyTitle>这个页面不存在</EmptyTitle>
          <EmptyDescription>链接可能已过期，或者对应的集已被删除。</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button nativeButton={false} render={<Link href="/" />}>回到概览</Button>
        </EmptyContent>
      </Empty>
    </div>
  );
}
