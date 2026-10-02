// PROTOTYPE — #2973 的共用小件：状态标记、统一浏览卡、「更多」菜单与写操作桩。
// 原型不写真实数据：所有写操作只弹一条提示。

import type { ReactNode } from "react";
import {
  Combine,
  History,
  ImageUp,
  Library,
  Maximize2,
  MoreHorizontal,
  PenLine,
  Sparkles,
  User,
  Landmark,
  Package,
  ShoppingBag,
} from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAppStore } from "@/stores/app-store";
import type { AssetSheetType } from "@/types/asset-sheet";
import type { ActionsMode } from "./proto-params";
import { STATUS_LABEL, type ProtoAsset, type ProtoStatus } from "./asset-model";

export function stub(action: string) {
  useAppStore.getState().pushToast(`原型：「${action}」不会写入数据`, "info");
}

export const TYPE_ICON: Record<AssetSheetType, typeof User> = {
  character: User,
  scene: Landmark,
  prop: Package,
  product: ShoppingBag,
};

export function StatusChip({ status, className }: { status: ProtoStatus; className?: string }) {
  if (status === "current") return null;
  const tone =
    status === "generating"
      ? "text-primary"
      : status === "stale"
        ? "text-warn"
        : status === "no-description"
          ? "text-warn"
          : "text-muted-foreground";
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 rounded-md bg-background/85 px-1.5 text-[11px] font-medium",
        tone,
        className,
      )}
    >
      <span
        aria-hidden
        className={cn("size-1.5 rounded-full bg-current", status === "generating" && "animate-breathe")}
      />
      {STATUS_LABEL[status]}
    </span>
  );
}

/** 资产的次要操作。`pinned` 模式下「查看大图」与「生成」常驻，其余进「更多」。 */
export function AssetMoreMenu({
  asset,
  includePinned,
  onView,
  align = "end",
}: {
  asset: ProtoAsset;
  includePinned: boolean;
  onView: () => void;
  align?: "start" | "end";
}) {
  const inLibrary = asset.type !== "product";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label={`${asset.name}：更多操作`} />}
        onClick={(e) => e.stopPropagation()}
      >
        <MoreHorizontal />
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="min-w-44" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuGroup>
          {includePinned && (
            <>
              <DropdownMenuItem onClick={onView} disabled={!asset.sheetUrl}>
                <Maximize2 /> 查看大图
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => stub(asset.sheetPath ? "重新生成" : "生成")}>
                <Sparkles /> {asset.sheetPath ? "重新生成资产图" : "生成资产图"}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuItem onClick={() => stub("上传资产图")}>
            <ImageUp /> 上传资产图
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => stub("局部修改")} disabled={!asset.sheetUrl}>
            <PenLine /> 局部修改
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => stub("版本历史")}>
            <History /> 版本历史
          </DropdownMenuItem>
        </DropdownMenuGroup>
        {inLibrary && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem onClick={() => stub("加入资产库")}>
                <Library /> 加入资产库
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => stub("并入…")}>
                <Combine /> 并入…
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function SheetImage({
  asset,
  className,
  fit = "contain",
}: {
  asset: Pick<ProtoAsset, "sheetUrl" | "name" | "type">;
  className?: string;
  fit?: "contain" | "cover";
}) {
  const Icon = TYPE_ICON[asset.type];
  return (
    <div className={cn("relative aspect-video overflow-hidden bg-muted/60", className)}>
      {asset.sheetUrl ? (
        <img
          src={asset.sheetUrl}
          alt={`${asset.name} 资产图`}
          loading="lazy"
          className={cn("size-full transition-opacity duration-200", fit === "contain" ? "object-contain" : "object-cover")}
        />
      ) : (
        <div className="grid size-full place-items-center text-muted-foreground">
          <Icon className="size-8 opacity-60" />
        </div>
      )}
    </div>
  );
}

/**
 * 统一浏览卡：四类资产共用。卡片本身只负责「看」和「选中」，编辑全部交给详情。
 * 整卡可点：名字是真正的按钮，用 after 伪元素铺满卡片；卡内其他按钮叠在它上面。
 */
export function BrowseCard({
  asset,
  status,
  actions,
  selected,
  onOpen,
  onView,
  meta,
}: {
  asset: ProtoAsset;
  status: ProtoStatus;
  actions: ActionsMode;
  selected?: boolean;
  onOpen: () => void;
  onView: () => void;
  meta?: ReactNode;
}) {
  const pinned = actions === "pinned";
  return (
    <article
      className={cn(
        "group/card relative flex flex-col overflow-hidden rounded-lg border bg-card transition-colors duration-150",
        selected ? "border-primary ring-1 ring-primary/40" : "border-border [@media(hover:hover)]:hover:border-input",
      )}
    >
      <div className="relative">
        <SheetImage asset={asset} />
        <StatusChip status={status} className="absolute left-2 top-2" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1 p-3 pt-2.5">
        <div className="flex min-w-0 items-center gap-1">
          <button
            type="button"
            onClick={onOpen}
            className="min-w-0 flex-1 truncate text-left text-[14px] font-medium text-foreground outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-2 focus-visible:after:ring-ring"
            title={asset.name}
          >
            {asset.name}
          </button>
          <div className="relative z-10 -mr-1.5 flex shrink-0 items-center">
            {pinned && (
              <>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`${asset.name}：查看大图`}
                  disabled={!asset.sheetUrl}
                  onClick={onView}
                >
                  <Maximize2 />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`${asset.name}：${asset.sheetPath ? "重新生成资产图" : "生成资产图"}`}
                  disabled={status === "generating" || status === "no-description"}
                  onClick={() => stub("生成")}
                >
                  <Sparkles />
                </Button>
              </>
            )}
            <AssetMoreMenu asset={asset} includePinned={!pinned} onView={onView} />
          </div>
        </div>
        <p className="line-clamp-2 text-[13px] leading-[1.5] text-muted-foreground">
          {asset.description || "还没有描述"}
        </p>
        {meta}
      </div>
    </article>
  );
}

export function CardMeta({ asset }: { asset: ProtoAsset }) {
  const parts: string[] = [];
  if (asset.derivatives.length) parts.push(`衍生 ${asset.derivatives.length}`);
  if (asset.aliases.length) parts.push(`别名 ${asset.aliases.length}`);
  if (asset.type === "product" && asset.referenceUrls.length) parts.push(`原图 ${asset.referenceUrls.length}`);
  if (!parts.length) return null;
  return <div className="mt-auto pt-1 text-[12px] tabular-nums text-muted-foreground">{parts.join(" · ")}</div>;
}
