// PROTOTYPE — #2973：画廊内的图片查看器。
// ←/→ 在当前画廊（按筛选后的顺序）的资产之间切换；顶部显示资产名、类型与序号；
// 底部是这张资产图的版本条，点选只切换查看，还原仍走版本历史。

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { cn } from "cn";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { TYPE_LABEL, type ProtoAsset } from "./asset-model";
import { stub } from "./parts";
import { useVersions } from "./use-versions";

export function AssetViewer({
  projectName,
  assets,
  index,
  onIndexChange,
  onClose,
  onEdit,
}: {
  projectName: string;
  /** 只含有资产图的资产，按画廊顺序。 */
  assets: ProtoAsset[];
  index: number | null;
  onIndexChange: (i: number) => void;
  onClose: () => void;
  onEdit?: (asset: ProtoAsset) => void;
}) {
  const open = index !== null && assets[index] !== undefined;
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-[oklch(0.12_0.005_265)] transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <DialogPrimitive.Popup
          data-proto-captures-arrows
          className="fixed inset-0 z-50 flex flex-col outline-none transition-opacity duration-150 data-ending-style:opacity-0 data-starting-style:opacity-0"
        >
          {open && (
            <ViewerBody
              projectName={projectName}
              assets={assets}
              index={index}
              onIndexChange={onIndexChange}
              onEdit={onEdit}
            />
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function ViewerBody({
  projectName,
  assets,
  index,
  onIndexChange,
  onEdit,
}: {
  projectName: string;
  assets: ProtoAsset[];
  index: number;
  onIndexChange: (i: number) => void;
  onEdit?: (asset: ProtoAsset) => void;
}) {
  const asset = assets[index];
  const versions = useVersions(projectName, asset.versionResource, asset.name);
  const [viewing, setViewing] = useState<number | null>(null);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- 原型：换资产时回到当前版本
  useEffect(() => setViewing(null), [asset.name]);

  const go = (dir: 1 | -1) => onIndexChange((index + dir + assets.length) % assets.length);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const shownVersion = viewing ?? versions?.current ?? null;
  const versionInfo = versions?.versions.find((v) => v.version === shownVersion);
  const src =
    viewing !== null && versionInfo?.file_url
      ? versionInfo.file_url
      : asset.sheetUrl!;
  const isCurrent = viewing === null || viewing === versions?.current;

  return (
    <>
      <header className="flex shrink-0 items-center gap-3 px-4 py-3 text-foreground">
        <DialogPrimitive.Title className="min-w-0 truncate text-[15px] font-semibold">{asset.name}</DialogPrimitive.Title>
        <span className="text-[12px] text-muted-foreground">{TYPE_LABEL[asset.type]}</span>
        <span className="text-[12px] tabular-nums text-muted-foreground">
          {index + 1} / {assets.length}
        </span>
        {versions && versions.versions.length > 0 && (
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-[12px] tabular-nums text-foreground">
            第 {shownVersion} 版{isCurrent ? "（当前）" : ""}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          {!isCurrent && (
            <Button variant="outline" size="sm" onClick={() => stub(`还原到第 ${shownVersion} 版`)}>
              还原到此版本
            </Button>
          )}
          {onEdit && (
            <Button variant="ghost" size="sm" onClick={() => onEdit(asset)}>
              编辑
            </Button>
          )}
          <DialogPrimitive.Close render={<Button variant="ghost" size="icon" aria-label="关闭" />}>
            <X />
          </DialogPrimitive.Close>
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1 items-center justify-center px-16">
        <img
          key={src}
          src={src}
          alt={`${asset.name} 资产图`}
          className="max-h-full max-w-full rounded-md object-contain transition-opacity duration-150 starting:opacity-0"
        />
        {assets.length > 1 && (
          <>
            <Button
              variant="secondary"
              size="icon-lg"
              aria-label="上一个资产"
              className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full"
              onClick={() => go(-1)}
            >
              <ChevronLeft />
            </Button>
            <Button
              variant="secondary"
              size="icon-lg"
              aria-label="下一个资产"
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full"
              onClick={() => go(1)}
            >
              <ChevronRight />
            </Button>
          </>
        )}
      </div>

      <footer className="flex shrink-0 justify-center gap-2 overflow-x-auto px-4 py-3">
        {versions?.versions.length
          ? [...versions.versions].reverse().map((v) => {
              const active = v.version === shownVersion;
              const thumb = v.file_url ?? API.getFileUrl(projectName, asset.sheetPath!);
              return (
                <button
                  key={v.version}
                  type="button"
                  onClick={() => setViewing(v.version)}
                  className={cn(
                    "relative w-24 shrink-0 overflow-hidden rounded-md border-2 transition-colors duration-150",
                    active ? "border-primary" : "border-transparent opacity-70 hover:opacity-100",
                  )}
                  aria-label={`查看第 ${v.version} 版`}
                  aria-pressed={active}
                >
                  <img src={thumb} alt="" className="aspect-video w-full object-cover" />
                  <span className="absolute bottom-0.5 left-1 rounded bg-background/80 px-1 text-[11px] tabular-nums text-foreground">
                    v{v.version}
                  </span>
                </button>
              );
            })
          : null}
      </footer>
    </>
  );
}
