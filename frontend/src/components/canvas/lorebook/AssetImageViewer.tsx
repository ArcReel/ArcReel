import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, ImageOff, Loader2, PenLine, RotateCcw } from "lucide-react";
import { cn } from "cn";
import { API, type VersionInfo } from "@/api";
import { CrossfadeImage } from "@/components/canvas/shared/CrossfadeImage";
import { TruncatedText } from "@/components/shared/TruncatedText";
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
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { VERSION_RESOURCE, type GalleryAsset } from "./gallery-model";

export interface AssetImageViewerProps {
  projectName: string;
  /** 可以查看的资产：有资产图的资产，按画廊筛选后的顺序。 */
  assets: readonly GalleryAsset[];
  /** 正在查看的资产名；为 null 或不在 `assets` 里时关闭。 */
  name: string | null;
  onNameChange: (name: string | null) => void;
  /** 「编辑」：打开这个资产的详情。只读时不传。 */
  onEdit?: (name: string) => void;
  /** 资产图被占用的资产：任务在跑，或卡片上有上传、版本恢复、删除在途。 */
  busyNames: ReadonlySet<string>;
  readOnly: boolean;
  onRestoreVersion?: () => Promise<void> | void;
}

type VersionsState =
  | { phase: "ready"; current: number; versions: VersionInfo[] }
  | { phase: "failed"; message: string };

/**
 * 画廊里的图片查看器：←/→ 按画廊筛选后的顺序在有资产图的资产之间切换，底部是这张资产图的版本条。
 * 点旧版本只切换查看；「还原到此版本」经单独确认后才改写资产图。
 */
export function AssetImageViewer({
  projectName,
  assets,
  name,
  onNameChange,
  onEdit,
  busyNames,
  readOnly,
  onRestoreVersion,
}: AssetImageViewerProps) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  const busyHintId = useId();
  const index = name === null ? -1 : assets.findIndex((item) => item.name === name);
  const live = index >= 0 ? assets[index] : undefined;
  // 关闭动画期间沿用最后一次查看的资产
  const [last, setLast] = useState(live);
  if (live && live !== last) setLast(live);
  const asset = live ?? last;
  const previous = index > 0 ? assets[index - 1].name : undefined;
  const next = index >= 0 && index < assets.length - 1 ? assets[index + 1].name : undefined;
  const busy = asset !== undefined && busyNames.has(asset.name);

  const [viewing, setViewing] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [versions, setVersions] = useState<VersionsState | null>(null);
  // 每次打开或换资产都从当前版本看起，版本条重新加载；关闭动画期间保持原样
  const liveName = live?.name ?? null;
  const [session, setSession] = useState<string | null>(null);
  if (liveName !== session) {
    setSession(liveName);
    if (liveName !== null) {
      setViewing(null);
      setConfirming(null);
      setRestoreError(null);
      setVersions(null);
    }
  }

  const type = asset?.type;
  const assetName = asset?.name;
  const sheetPath = asset?.sheetPath ?? null;
  const fingerprint = useProjectsStore((s) => (sheetPath ? s.getAssetFingerprint(sheetPath) : null));
  const currentUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, fingerprint) : null;
  // 资产图换了（生成、上传、还原）就重新读版本记录；读取期间沿用上一次的结果
  const [reload, setReload] = useState(0);
  const open = live !== undefined;
  useEffect(() => {
    if (!open || type === undefined || assetName === undefined) return;
    const controller = new AbortController();
    API.getVersions(projectName, VERSION_RESOURCE[type], assetName, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) {
          setVersions({ phase: "ready", current: data.current_version, versions: data.versions });
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setVersions({ phase: "failed", message: errMsg(err) });
      });
    return () => controller.abort();
  }, [open, projectName, type, assetName, fingerprint, reload]);

  if (!asset) return null;

  const current = versions?.phase === "ready" ? versions.current : 0;
  const list = versions?.phase === "ready" ? versions.versions : [];
  const viewedInfo = viewing !== null ? list.find((v) => v.version === viewing) : undefined;
  // 正在看的不是当前版本时，图片换成那个版本的文件
  const oldInfo = viewedInfo !== undefined && !viewedInfo.is_current ? viewedInfo : undefined;
  const shownVersion = viewedInfo?.version ?? (current > 0 ? current : null);
  const src = oldInfo ? (oldInfo.file_url ?? null) : currentUrl;
  const restorable = oldInfo !== undefined && !readOnly && oldInfo.restorable !== false;

  const navigate = (to: string | undefined) => {
    if (to !== undefined && !restoring) onNameChange(to);
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // 还原确认框嵌在查看器里，它的按键也会冒泡到这里
    if (confirming !== null || restoring) return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const to = event.key === "ArrowLeft" ? previous : event.key === "ArrowRight" ? next : undefined;
    if (to === undefined) return;
    event.preventDefault();
    navigate(to);
  };

  const requestRestore = () => {
    if (!oldInfo || busy || restoring) return;
    if (rejectIfAssetBusy(asset.type, projectName, asset.name, t, "assets:viewer_restore_busy_hint")) return;
    setRestoreError(null);
    setConfirming(oldInfo.version);
  };

  const executeRestore = async (version: number) => {
    if (busy || restoring) return;
    // 确认框打开期间资产图可能刚被别处占用，提交前再读一次
    if (rejectIfAssetBusy(asset.type, projectName, asset.name, t, "assets:viewer_restore_busy_hint")) return;
    setRestoring(true);
    setRestoreError(null);
    try {
      const result = await API.restoreVersion(projectName, VERSION_RESOURCE[asset.type], asset.name, version);
      if (result.asset_fingerprints) {
        useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
      }
      await onRestoreVersion?.();
      setReload((n) => n + 1);
      // 还原的版本成了当前版本，回到看当前；成功不弹提示
      setViewing(null);
      setConfirming(null);
    } catch (err) {
      setRestoreError(t("assets:viewer_restore_failed", { message: errMsg(err) }));
    } finally {
      setRestoring(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        // 还原在途时不响应关闭，请求落定前画廊的其他入口不能再写这张资产图
        if (!nextOpen && !restoring) onNameChange(null);
      }}
    >
      <DialogContent size="viewer" onKeyDown={handleKeyDown}>
        <DialogHeader>
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
            <DialogTitle className="min-w-0 max-w-full">
              <TruncatedText text={asset.name} />
            </DialogTitle>
            <p className="flex shrink-0 items-center gap-3 text-sm text-muted-foreground">
              <span>{t(`assets:viewer_type.${asset.type}`)}</span>
              <span className="tabular-nums">{t("assets:viewer_position", { index: index + 1, total: assets.length })}</span>
            </p>
            {shownVersion !== null && (
              <span className="flex shrink-0 items-center gap-2">
                <Badge variant={oldInfo ? "outline" : "secondary"}>
                  <span className="tabular-nums">{t("assets:viewer_version", { version: shownVersion })}</span>
                </Badge>
                {oldInfo ? (
                  <span className="text-sm text-subtle-foreground">
                    {t("assets:viewer_viewing_old", { version: current })}
                  </span>
                ) : (
                  <span className="text-sm text-subtle-foreground">{t("assets:viewer_current")}</span>
                )}
              </span>
            )}
            <div className="ml-auto flex shrink-0 items-center gap-2">
              {restorable && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy || restoring}
                  aria-describedby={busy ? busyHintId : undefined}
                  onClick={requestRestore}
                >
                  <RotateCcw aria-hidden data-icon="inline-start" />
                  {t("assets:viewer_restore")}
                </Button>
              )}
              {onEdit && (
                <Button variant="ghost" size="sm" disabled={restoring} onClick={() => onEdit(asset.name)}>
                  <PenLine aria-hidden data-icon="inline-start" />
                  {t("common:edit")}
                </Button>
              )}
            </div>
          </div>
          {restorable && busy && (
            <p id={busyHintId} className="text-xs text-subtle-foreground">
              {t("assets:viewer_restore_busy_hint")}
            </p>
          )}
        </DialogHeader>

        <DialogBody>
          <div className="flex h-full min-h-0 items-center gap-3">
            <Button
              variant="outline"
              size="icon-lg"
              disabled={previous === undefined || restoring}
              onClick={() => navigate(previous)}
              aria-label={t("assets:editor_previous")}
            >
              <ChevronLeft aria-hidden />
            </Button>
            <div className="relative flex size-full min-w-0 items-center justify-center">
              <CrossfadeImage
                src={src}
                alt={
                  shownVersion !== null
                    ? t("assets:viewer_image_alt_version", { name: asset.name, version: shownVersion })
                    : t("assets:viewer_image_alt", { name: asset.name })
                }
                className="object-contain"
                fallback={<ImageOff aria-hidden className="size-10 text-muted-foreground" />}
              />
            </div>
            <Button
              variant="outline"
              size="icon-lg"
              disabled={next === undefined || restoring}
              onClick={() => navigate(next)}
              aria-label={t("assets:editor_next")}
            >
              <ChevronRight aria-hidden />
            </Button>
          </div>
        </DialogBody>

        <DialogFooter>
          <VersionStrip
            state={versions}
            shownVersion={shownVersion}
            disabled={restoring}
            onSelect={setViewing}
          />
        </DialogFooter>

        <AlertDialog
          open={confirming !== null}
          onOpenChange={(nextOpen) => {
            if (!nextOpen && !restoring) setConfirming(null);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("assets:viewer_restore_title", { version: confirming ?? 0 })}</AlertDialogTitle>
            </AlertDialogHeader>
            <AlertDialogBody>
              <div className="flex flex-col gap-3">
                <AlertDialogDescription>
                  {t("assets:viewer_restore_description", { name: asset.name, version: confirming ?? 0 })}
                </AlertDialogDescription>
                {restoreError && (
                  <p role="alert" className="text-destructive">
                    {restoreError}
                  </p>
                )}
              </div>
            </AlertDialogBody>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={restoring}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction
                disabled={restoring || busy}
                onClick={() => confirming !== null && void executeRestore(confirming)}
              >
                {restoring && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                {t("assets:viewer_restore_confirm")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}

/** 底部版本条：每个版本一张缩略图，点选只切换查看。 */
function VersionStrip({
  state,
  shownVersion,
  disabled,
  onSelect,
}: {
  state: VersionsState | null;
  shownVersion: number | null;
  disabled: boolean;
  onSelect: (version: number) => void;
}) {
  const { t } = useTranslation(["assets", "dashboard", "common"]);
  if (state === null) {
    return <p className="min-w-0 flex-1 text-sm text-subtle-foreground">{t("common:loading")}</p>;
  }
  if (state.phase === "failed") {
    return (
      <p role="alert" className="min-w-0 flex-1 text-sm text-destructive">
        {t("assets:viewer_versions_failed", { message: state.message })}
      </p>
    );
  }
  const versions = state.versions;
  if (versions.length === 0) {
    return <p className="min-w-0 flex-1 text-sm text-subtle-foreground">{t("dashboard:no_history")}</p>;
  }
  return (
    <ul
      aria-label={t("dashboard:history_versions")}
      className="scroll-fade-x relative flex min-w-0 flex-1 gap-2 overflow-x-auto py-1"
    >
      {versions.map((v) => {
        const active = v.version === shownVersion;
        return (
          <li key={v.version} className="shrink-0">
            <button
              type="button"
              disabled={disabled}
              aria-pressed={active}
              aria-label={
                v.is_current
                  ? t("assets:viewer_version_current", { version: v.version })
                  : t("assets:viewer_version", { version: v.version })
              }
              onClick={() => onSelect(v.version)}
              className={cn(
                "focus-ring relative flex w-24 flex-col overflow-hidden rounded-md border-2 bg-background transition-colors duration-fast disabled:cursor-not-allowed disabled:opacity-50",
                active ? "border-primary" : "border-transparent hover:border-input",
              )}
            >
              {v.file_url ? (
                <img src={v.file_url} alt="" loading="lazy" className="aspect-video w-full object-cover" />
              ) : (
                <span className="flex aspect-video w-full items-center justify-center text-muted-foreground">
                  <ImageOff aria-hidden className="size-4" />
                </span>
              )}
              <span className="flex items-center justify-center gap-1 py-0.5 text-xs text-foreground tabular-nums">
                v{v.version}
                {v.is_current && <span aria-hidden className="size-1.5 rounded-full bg-primary" />}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
