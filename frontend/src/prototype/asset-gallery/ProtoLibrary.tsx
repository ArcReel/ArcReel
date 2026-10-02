// PROTOTYPE — #2973：全局资产库卡片点击后的行为。
//   B 点击看详情：右侧 Sheet 先展示大图与全部信息，详情内点「编辑」才进入表单
//   C 点击直接编辑：沿用现有的资产表单弹窗
// 本地资产库为空时，用一个本地项目的角色、场景、道具冒充库内资产（标「原型样例」）。

import { useEffect, useMemo, useState } from "react";
import { MoreHorizontal, Pencil, Trash2, X, FolderInput } from "lucide-react";
import { cn } from "cn";
import { API } from "@/api";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import type { Asset, AssetType } from "@/types/asset";
import { TYPE_LABEL } from "./asset-model";
import { SheetImage, stub } from "./parts";
import { useProtoParams } from "./proto-params";

const SAMPLE_PROJECT = "proj-f02d4dd8";

interface LibItem {
  asset: Asset;
  imageUrl: string | null;
  sample: boolean;
}

function useSampleItems(type: AssetType, enabled: boolean): LibItem[] {
  const [items, setItems] = useState<LibItem[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    API.getProject(SAMPLE_PROJECT)
      .then(({ project }) => {
        const src =
          type === "character" ? project.characters : type === "scene" ? project.scenes : project.props;
        const next = Object.entries(src ?? {}).map(([name, raw], i): LibItem => {
          const r = raw as { description: string; voice_style?: string; character_sheet?: string; scene_sheet?: string; prop_sheet?: string };
          const sheet = r.character_sheet ?? r.scene_sheet ?? r.prop_sheet ?? null;
          return {
            asset: {
              id: `sample-${type}-${i}`,
              type,
              name,
              description: r.description,
              voice_style: r.voice_style ?? "",
              image_path: sheet,
              audio_path: null,
              source_project: project.title ?? SAMPLE_PROJECT,
              updated_at: "2026-09-28T10:00:00Z",
              derivatives:
                type === "character" && i === 0
                  ? [{ name: "便装", description: "换成灰色连帽卫衣与牛仔裤，其余不变。", image_path: sheet }]
                  : [],
            },
            imageUrl: sheet ? API.getFileUrl(SAMPLE_PROJECT, sheet) : null,
            sample: true,
          };
        });
        if (alive) setItems(next);
      })
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [type, enabled]);
  return items;
}

export function ProtoLibraryGrid({ assets, activeTab }: { assets: Asset[]; activeTab: AssetType }) {
  const { variant } = useProtoParams();
  const samples = useSampleItems(activeTab, assets.length === 0);
  const items: LibItem[] = useMemo(
    () =>
      assets.length
        ? assets.map((a) => ({ asset: a, imageUrl: API.getGlobalAssetUrl(a.image_path, a.updated_at), sample: false }))
        : samples,
    [assets, samples],
  );

  const [detail, setDetail] = useState<LibItem | null>(null);
  const [editing, setEditing] = useState<LibItem | null>(null);
  const [deleting, setDeleting] = useState<LibItem | null>(null);

  const open = (it: LibItem) => (variant === "B" ? setDetail(it) : setEditing(it));

  return (
    <>
      {items.length > 0 && items[0].sample && (
        <p className="mb-3 text-[12px] text-muted-foreground">本地资产库为空，以下是用本地项目冒充的原型样例。</p>
      )}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
        {items.map((it) => (
          <LibraryCard
            key={it.asset.id}
            item={it}
            primaryLabel={variant === "B" ? "查看详情" : "编辑"}
            onOpen={() => open(it)}
            onEdit={() => setEditing(it)}
            onDelete={() => setDeleting(it)}
          />
        ))}
      </div>

      <Sheet open={detail !== null} onOpenChange={(o) => !o && setDetail(null)}>
        <SheetContent side="right" showCloseButton={false} className="gap-0 p-0 data-[side=right]:w-[min(520px,92vw)] data-[side=right]:sm:max-w-none">
          {detail && (
            <LibraryDetail
              item={detail}
              onClose={() => setDetail(null)}
              onEdit={() => setEditing(detail)}
              onDelete={() => setDeleting(detail)}
            />
          )}
        </SheetContent>
      </Sheet>

      {editing && (
        <AssetFormModal
          type={editing.asset.type}
          mode="edit"
          initialData={editing.asset}
          previewImageUrl={editing.imageUrl ?? undefined}
          derivatives={editing.asset.derivatives}
          onClose={() => setEditing(null)}
          onSubmit={() => {
            stub("保存资产");
            setEditing(null);
            return Promise.resolve();
          }}
        />
      )}

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除{deleting ? TYPE_LABEL[deleting.asset.type] : ""}「{deleting?.asset.name}」？</AlertDialogTitle>
            <AlertDialogDescription>只从资产库删除，已应用到项目里的副本不受影响。删除后无法恢复。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <Button
              variant="destructive"
              onClick={() => {
                stub("删除");
                setDeleting(null);
                setDetail(null);
              }}
            >
              删除
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function LibraryCard({
  item,
  primaryLabel,
  onOpen,
  onEdit,
  onDelete,
}: {
  item: LibItem;
  primaryLabel: string;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const a = item.asset;
  return (
    <article className="group/card relative flex flex-col overflow-hidden rounded-lg border border-border bg-card transition-colors duration-150 [@media(hover:hover)]:hover:border-input">
      <SheetImage asset={{ sheetUrl: item.imageUrl, name: a.name, type: a.type }} />
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-3 pt-2.5">
        <div className="flex min-w-0 items-center gap-1">
          <button
            type="button"
            onClick={onOpen}
            aria-label={`${a.name}：${primaryLabel}`}
            className="min-w-0 flex-1 truncate text-left text-[14px] font-medium text-foreground outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-2 focus-visible:after:ring-ring"
          >
            {a.name}
          </button>
          <div className="relative z-10 -mr-1.5">
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`${a.name}：更多操作`} />}>
                <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-36">
                <DropdownMenuGroup>
                  <DropdownMenuItem onClick={onEdit}>
                    <Pencil /> 编辑
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => stub("应用到项目")}>
                    <FolderInput /> 应用到项目…
                  </DropdownMenuItem>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={onDelete}>
                  <Trash2 /> 删除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        <p className="line-clamp-2 text-[13px] leading-[1.5] text-muted-foreground">{a.description || "还没有描述"}</p>
        {a.derivatives.length > 0 && (
          <div className="mt-auto pt-1 text-[12px] text-muted-foreground">衍生 {a.derivatives.length}</div>
        )}
      </div>
    </article>
  );
}

function LibraryDetail({
  item,
  onClose,
  onEdit,
  onDelete,
}: {
  item: LibItem;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const a = item.asset;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
        <SheetTitle className="min-w-0 flex-1 truncate text-[16px] font-semibold">{a.name}</SheetTitle>
        <span className="text-[12px] text-muted-foreground">{TYPE_LABEL[a.type]}</span>
        <Button variant="ghost" size="icon-sm" aria-label="关闭" onClick={onClose}>
          <X />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-5 p-4">
          <SheetImage asset={{ sheetUrl: item.imageUrl, name: a.name, type: a.type }} className="rounded-lg border border-border" />
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[13px]">
            <dt className="text-muted-foreground">来源项目</dt>
            <dd className="text-foreground">{a.source_project ?? "—"}</dd>
            <dt className="text-muted-foreground">更新于</dt>
            <dd className="tabular-nums text-foreground">{a.updated_at ? new Date(a.updated_at).toLocaleDateString("zh-CN") : "—"}</dd>
          </dl>
          <section className="flex flex-col gap-1.5">
            <h3 className="text-[12px] font-medium text-muted-foreground">描述</h3>
            <p className="whitespace-pre-wrap text-[13px] leading-[1.6] text-foreground">{a.description || "还没有描述"}</p>
          </section>
          {a.type === "character" && a.voice_style && (
            <section className="flex flex-col gap-1.5">
              <h3 className="text-[12px] font-medium text-muted-foreground">声音</h3>
              <p className="text-[13px] text-foreground">{a.voice_style}</p>
            </section>
          )}
          {a.derivatives.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="text-[12px] font-medium text-muted-foreground">衍生（{a.derivatives.length}）</h3>
              <ul className="grid grid-cols-2 gap-3">
                {a.derivatives.map((d) => (
                  <li key={d.name} className="flex flex-col gap-1">
                    <SheetImage
                      asset={{
                        sheetUrl: d.image_path ? (item.sample ? API.getFileUrl(SAMPLE_PROJECT, d.image_path) : API.getGlobalAssetUrl(d.image_path)) : null,
                        name: d.name,
                        type: "character",
                      }}
                      className="rounded-md border border-border"
                    />
                    <span className="text-[13px] font-medium text-foreground">{d.name}</span>
                    <span className="line-clamp-2 text-[12px] text-muted-foreground">{d.description}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
      <footer className={cn("flex shrink-0 items-center gap-2 border-t border-border px-4 py-3")}>
        <Button variant="destructive" onClick={onDelete}>
          <Trash2 /> 删除
        </Button>
        <div className="flex-1" />
        <Button variant="outline" onClick={() => stub("应用到项目")}>
          <FolderInput /> 应用到项目…
        </Button>
        <Button onClick={onEdit}>
          <Pencil /> 编辑
        </Button>
      </footer>
    </div>
  );
}
