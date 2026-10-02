// PROTOTYPE — #2973：画廊页的 B / C / D 三种编辑方式，挂在真实的
// /characters、/scenes、/props、/products 路由上，读真实项目数据，写操作全部是桩。
//   B 浏览卡 + 右侧 Sheet：网格只负责浏览，点卡片从右侧滑出编辑面板
//   C 主从分栏：左侧窄列表，右侧常驻详情，宽窗口下详情左图右表单
//   D 网格内展开：点卡片在它所在行的下方展开整行宽的编辑区

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, Package, Plus, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAssetSheetStatus, useSheetStatusByName } from "@/components/canvas/lorebook/useAssetSheetStatus";
import { useProjectsStore } from "@/stores/projects-store";
import type { AssetSheetType } from "@/types/asset-sheet";
import type { Character, Product, Prop, Scene } from "@/types";
import { buildAssets, statusOf, TYPE_LABEL, type ProtoAsset, type ProtoStatus } from "./asset-model";
import { AssetDetailEditor, draftOf, isDirty, type Draft } from "./AssetDetailEditor";
import { AssetViewer } from "./AssetViewer";
import { BrowseCard, CardMeta, SheetImage, StatusChip, stub } from "./parts";
import { useProtoParams } from "./proto-params";

type Filter = "all" | "pending" | "stale";

export function ProtoGallery({
  projectName,
  type,
  data,
  generatingNames,
}: {
  projectName: string;
  type: AssetSheetType;
  data: Record<string, Character | Scene | Prop | Product>;
  generatingNames?: Set<string>;
}) {
  const { variant, actions } = useProtoParams();
  const fp = useProjectsStore((s) => s.getAssetFingerprint);
  const assets = useMemo(() => buildAssets(projectName, type, data, fp), [projectName, type, data, fp]);
  const rows = useAssetSheetStatus(projectName);
  const byName = useSheetStatusByName(rows, type);
  const statusMap = useMemo(() => {
    const m = new Map<string, ProtoStatus>();
    for (const a of assets) m.set(a.name, statusOf(a, byName.get(a.name), Boolean(generatingNames?.has(a.name))));
    return m;
  }, [assets, byName, generatingNames]);
  const [filter, setFilter] = useState<Filter>("all");
  const shown = assets.filter((a) => {
    const s = statusMap.get(a.name);
    if (filter === "pending") return s === "missing" || s === "no-description";
    if (filter === "stale") return s === "stale";
    return true;
  });

  // 查看器只在有图的资产间切换
  const viewable = shown.filter((a) => a.sheetUrl);
  const [viewIndex, setViewIndex] = useState<number | null>(null);
  const view = (a: ProtoAsset) => {
    const i = viewable.findIndex((v) => v.name === a.name);
    if (i >= 0) setViewIndex(i);
  };

  // 选中与草稿：同一时刻只编辑一个资产；带草稿离开时弹三按钮拦截
  const [selected, setSelected] = useState<string | null>(variant === "C" ? (shown[0]?.name ?? null) : null);
  const selectedAsset = assets.find((a) => a.name === selected) ?? null;
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型：切换选中时载入草稿
    setDraft(selectedAsset ? draftOf(selectedAsset) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型：切换方案时主从默认选中第一个
    if (variant === "C" && !selected && shown[0]) setSelected(shown[0].name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variant]);

  const dirty = Boolean(selectedAsset && draft && isDirty(selectedAsset, draft));
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const guard = (next: () => void) => {
    if (dirty) setPendingLeave(() => next);
    else next();
  };
  const select = (name: string | null) => guard(() => setSelected(name));

  const editorFor = (a: ProtoAsset, extra?: { headerEnd?: ReactNode; headerStart?: ReactNode; className?: string }) =>
    draft && (
      <AssetDetailEditor
        key={a.name}
        asset={a}
        status={statusMap.get(a.name) ?? "current"}
        projectName={projectName}
        actions={actions}
        draft={draft}
        onDraftChange={setDraft}
        onView={() => view(a)}
        {...extra}
      />
    );

  const toolbar = (
    <GalleryBar
      type={type}
      count={assets.length}
      filter={filter}
      onFilter={setFilter}
      pendingCount={assets.filter((a) => ["missing", "no-description"].includes(statusMap.get(a.name)!)).length}
      staleCount={assets.filter((a) => statusMap.get(a.name) === "stale").length}
    />
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {toolbar}

      {variant === "B" && (
        <>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <CardGrid>
              {shown.map((a) => (
                <BrowseCard
                  key={a.name}
                  asset={a}
                  status={statusMap.get(a.name)!}
                  actions={actions}
                  selected={a.name === selected}
                  onOpen={() => select(a.name)}
                  onView={() => view(a)}
                  meta={<CardMeta asset={a} />}
                />
              ))}
            </CardGrid>
          </div>
          <Sheet open={Boolean(selectedAsset)} onOpenChange={(o) => !o && select(null)}>
            <SheetContent
              side="right"
              showCloseButton={false}
              data-proto-captures-arrows
              className="gap-0 p-0 data-[side=right]:w-[min(560px,92vw)] data-[side=right]:sm:max-w-none"
            >
              {selectedAsset &&
                editorFor(selectedAsset, {
                  className: "h-full",
                  headerEnd: (
                    <>
                      <StepButtons list={shown} current={selectedAsset.name} onStep={(n) => select(n)} />
                      <Button variant="ghost" size="icon-sm" aria-label="关闭" onClick={() => select(null)}>
                        <X />
                      </Button>
                    </>
                  ),
                })}
            </SheetContent>
          </Sheet>
        </>
      )}

      {variant === "C" && (
        <div className="flex min-h-0 flex-1">
          <nav aria-label={`${TYPE_LABEL[type]}列表`} className="flex w-72 shrink-0 flex-col border-r border-border">
            <ul className="min-h-0 flex-1 overflow-y-auto p-2">
              {shown.map((a) => (
                <li key={a.name}>
                  <ListRow
                    asset={a}
                    status={statusMap.get(a.name)!}
                    selected={a.name === selected}
                    dirty={a.name === selected && dirty}
                    onClick={() => select(a.name)}
                  />
                </li>
              ))}
            </ul>
          </nav>
          <div className="flex min-w-0 flex-1 flex-col">
            {selectedAsset ? (
              editorFor(selectedAsset, { className: "h-full" })
            ) : (
              <div className="grid flex-1 place-items-center text-[13px] text-muted-foreground">从左侧选择一个{TYPE_LABEL[type]}</div>
            )}
          </div>
        </div>
      )}

      {variant === "D" && (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <ExpandGrid
            assets={shown}
            selected={selected}
            renderCard={(a) => (
              <BrowseCard
                asset={a}
                status={statusMap.get(a.name)!}
                actions={actions}
                selected={a.name === selected}
                onOpen={() => select(a.name === selected ? null : a.name)}
                onView={() => view(a)}
                meta={<CardMeta asset={a} />}
              />
            )}
            renderPanel={(a) => (
              <div className="overflow-hidden rounded-lg border border-primary/40 bg-card">
                {editorFor(a, {
                  className: "max-h-[min(720px,80dvh)]",
                  headerEnd: (
                    <Button variant="ghost" size="icon-sm" aria-label="收起" onClick={() => select(null)}>
                      <X />
                    </Button>
                  ),
                })}
              </div>
            )}
          />
        </div>
      )}

      <AssetViewer
        projectName={projectName}
        assets={viewable}
        index={viewIndex}
        onIndexChange={setViewIndex}
        onClose={() => setViewIndex(null)}
        onEdit={(a) => {
          setViewIndex(null);
          select(a.name);
        }}
      />

      <AlertDialog open={pendingLeave !== null} onOpenChange={(o) => !o && setPendingLeave(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>「{selectedAsset?.name}」有未保存的修改</AlertDialogTitle>
            <AlertDialogDescription>离开前要保存这些修改吗？</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <Button
              variant="outline"
              onClick={() => {
                const next = pendingLeave;
                setPendingLeave(null);
                next?.();
              }}
            >
              不保存
            </Button>
            <Button
              onClick={() => {
                stub("保存");
                const next = pendingLeave;
                setPendingLeave(null);
                next?.();
              }}
            >
              保存
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function CardGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">{children}</div>;
}

function GalleryBar({
  type,
  count,
  filter,
  onFilter,
  pendingCount,
  staleCount,
}: {
  type: AssetSheetType;
  count: number;
  filter: Filter;
  onFilter: (f: Filter) => void;
  pendingCount: number;
  staleCount: number;
}) {
  const label = TYPE_LABEL[type];
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border px-5 py-2.5">
      <h2 className="text-[15px] font-semibold text-foreground">{label}</h2>
      <span className="text-[13px] tabular-nums text-muted-foreground">{count}</span>
      <ToggleGroup
        value={[filter]}
        onValueChange={(v: string[]) => v[0] && onFilter(v[0] as Filter)}
        variant="outline"
        size="sm"
      >
        <ToggleGroupItem value="all">全部</ToggleGroupItem>
        <ToggleGroupItem value="pending">待生成 {pendingCount}</ToggleGroupItem>
        <ToggleGroupItem value="stale">已过期 {staleCount}</ToggleGroupItem>
      </ToggleGroup>
      <div className="flex-1" />
      {pendingCount > 0 && (
        <Button variant="outline" size="sm" onClick={() => stub("批量生成")}>
          批量生成 {pendingCount} 张
        </Button>
      )}
      {type !== "product" && (
        <Button variant="outline" size="sm" onClick={() => stub("从资产库选择")}>
          <Package /> 从资产库选择
        </Button>
      )}
      <Button size="sm" onClick={() => stub(`新增${label}`)}>
        <Plus /> 新增{label}
      </Button>
    </div>
  );
}

function ListRow({
  asset,
  status,
  selected,
  dirty,
  onClick,
}: {
  asset: ProtoAsset;
  status: ProtoStatus;
  selected: boolean;
  dirty: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg p-1.5 text-left outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring",
        selected ? "bg-muted" : "[@media(hover:hover)]:hover:bg-muted/50",
      )}
    >
      <SheetImage asset={asset} className="w-20 shrink-0 rounded-md" fit="cover" />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[13px] font-medium text-foreground">{asset.name}</span>
          {dirty && <span aria-label="有未保存的修改" className="size-1.5 shrink-0 rounded-full bg-primary" />}
        </span>
        <StatusChip status={status} className="self-start bg-transparent px-0" />
        {status === "current" && asset.derivatives.length > 0 && (
          <span className="text-[12px] text-muted-foreground">衍生 {asset.derivatives.length}</span>
        )}
      </span>
    </button>
  );
}

function StepButtons({ list, current, onStep }: { list: ProtoAsset[]; current: string; onStep: (name: string) => void }) {
  const i = list.findIndex((a) => a.name === current);
  return (
    <>
      <Button variant="ghost" size="icon-sm" aria-label="上一个" disabled={i <= 0} onClick={() => onStep(list[i - 1].name)}>
        <ChevronUp />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="下一个"
        disabled={i < 0 || i >= list.length - 1}
        onClick={() => onStep(list[i + 1].name)}
      >
        <ChevronDown />
      </Button>
    </>
  );
}

/** 网格内展开：量出当前列数，把编辑区插在选中卡片所在行之后，占满整行。 */
function ExpandGrid({
  assets,
  selected,
  renderCard,
  renderPanel,
}: {
  assets: ProtoAsset[];
  selected: string | null;
  renderCard: (a: ProtoAsset) => ReactNode;
  renderPanel: (a: ProtoAsset) => ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(1);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setCols(getComputedStyle(el).gridTemplateColumns.split(" ").filter(Boolean).length || 1);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const idx = assets.findIndex((a) => a.name === selected);
  const insertAfter = idx < 0 ? -1 : Math.min(assets.length - 1, Math.floor(idx / cols) * cols + cols - 1);
  const panelAsset = idx >= 0 ? assets[idx] : null;

  useEffect(() => {
    if (!panelAsset) return;
    document.getElementById("proto-expand-panel")?.scrollIntoView({ block: "nearest" });
  }, [panelAsset]);

  return (
    <div ref={ref} className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
      {assets.map((a, i) => (
        <FragmentWithPanel key={a.name} card={renderCard(a)} panel={i === insertAfter && panelAsset ? renderPanel(panelAsset) : null} />
      ))}
    </div>
  );
}

function FragmentWithPanel({ card, panel }: { card: ReactNode; panel: ReactNode }) {
  return (
    <>
      {card}
      {panel && (
        <div id="proto-expand-panel" className="col-span-full scroll-mt-4">
          {panel}
        </div>
      )}
    </>
  );
}

