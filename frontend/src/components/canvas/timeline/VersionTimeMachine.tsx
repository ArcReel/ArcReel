import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Download, History, Loader2, Trash2 } from "lucide-react";
import { cn } from "cn";
import { API, type VersionInfo } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { dismissOnEscape, dismissOnFocusLeave } from "@/utils/inline-confirm";
import { useTrackWrite } from "@/components/canvas/lorebook/useAssetWrites";
import { PresentationPlayer } from "@/components/shared/PresentationPlayer";

interface VersionTimeMachineProps {
  projectName: string;
  resourceType: "storyboards" | "videos" | "audio" | "characters" | "character_derivatives" | "scenes" | "props" | "products" | "reference_videos" | "grids";
  resourceId: string;
  onRestore?: (version: number) => void | Promise<unknown>;
  /** Icon-only trigger button: hides label and chevron for narrow card headers. */
  iconOnly?: boolean;
  /** Allow preview/download history without exposing the restore mutation. */
  readOnly?: boolean;
  /**
   * 同资源正被生成/编辑占用（含 image_edit 乐观占用）：禁用版本恢复。
   * image_edit 任务完成时会无条件把 current 覆盖为编辑结果，占用期间恢复旧版本会
   * 显示成功但随后被编辑任务覆盖，用户最后一次选择丢失。
   */
  busy?: boolean;
  /**
   * 版本恢复或删除的在途状态回传父级：`busy` 只做「外部占用 → 禁写入」这一向，兄弟控件
   * （生成、上传）还需反向知道恢复正在写同一个资源文件，否则恢复返回前它们仍可点，
   * 两个请求并发写同一路径、后完成者覆盖前者且双方都提示成功。
   */
  onRestoringChange?: (restoring: boolean) => void;
  /**
   * 提交时刻的占用复核（新鲜读）：`busy` 是最近一次渲染的快照，版本面板打开期间
   * Agent 入队、批量入口或轮询落库都可能占用该资源，新 prop 冲刷到按钮之前的点击
   * 仍会发出恢复请求，与在跑的任务并发写同一个资源文件。返回 true 即拒绝本次恢复。
   */
  checkBusy?: () => boolean;
  /**
   * 受控打开：由外部（如卡片的「更多」菜单）决定开合，不渲染自己的触发按钮，
   * 弹层按 `anchor` 定位，关闭后焦点回到它。
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  anchor?: RefObject<HTMLElement | null>;
}

function getImagePreviewHeightClass(
  resourceType: VersionTimeMachineProps["resourceType"],
): string {
  if (resourceType === "characters" || resourceType === "character_derivatives") return "h-80";
  if (resourceType === "scenes" || resourceType === "props" || resourceType === "products") return "h-56";
  return "h-64";
}

// 预览选中态记在所属资源身份与版本指纹之下：资源或其版本改变后不沿用另一份资源的选中
interface VersionSelection {
  projectName: string;
  resourcePath: string;
  fingerprint: number | null;
  version: number;
}

interface VersionDeleteButtonProps {
  version: number;
  /** 不可删除的原因（当前版本、资源占用）；非空时图标置灰并说明，保留聚焦让键盘和读屏读得到原因。 */
  disabledReason: string | null;
  /** 同一面板有别的版本写请求在途：暂时不可操作，请求结束即恢复，不另作说明。 */
  blocked: boolean;
  deleting: boolean;
  onConfirm: () => Promise<void>;
}

/** 版本旁的删除图标：点一下原地变成确认按钮，再点才移入项目回收站；Esc 或焦点离开即取消。 */
function VersionDeleteButton({ version, disabledReason, blocked, deleting, onConfirm }: VersionDeleteButtonProps) {
  const { t } = useTranslation("dashboard");
  const [confirming, setConfirming] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef(false);

  useEffect(() => {
    if (confirming) {
      confirmRef.current?.focus();
    } else if (returnFocusRef.current) {
      returnFocusRef.current = false;
      triggerRef.current?.focus();
    }
  }, [confirming]);

  const dismiss = (returnFocus: boolean) => {
    if (deleting) return;
    returnFocusRef.current = returnFocus;
    setConfirming(false);
  };

  if (confirming) {
    return (
      <Button
        ref={confirmRef}
        variant="destructive"
        size="xs"
        aria-label={t("version_delete_confirm_label", { version })}
        // 确认期间版本变成当前版本或资源被占用时实时禁用；删除成功后这一项随版本移出而卸载
        disabled={deleting || blocked || disabledReason !== null}
        onBlur={(event) => dismissOnFocusLeave(event, dismiss)}
        onKeyDown={(event) => dismissOnEscape(event, dismiss)}
        onClick={(event) => {
          // 双击图标的第二下落在同一位置，不算确认
          if (event.detail > 1) return;
          void onConfirm().finally(() => {
            returnFocusRef.current = true;
            setConfirming(false);
          });
        }}
      >
        {deleting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
        {t("version_delete_confirm")}
      </Button>
    );
  }

  const label = disabledReason ?? t("version_delete_label", { version });
  return (
    <span className={cn("inline-flex", disabledReason !== null && "cursor-not-allowed opacity-50")}>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              ref={triggerRef}
              variant="ghost"
              size="icon-xs"
              aria-label={label}
              aria-disabled={disabledReason !== null || undefined}
              disabled={blocked}
              onClick={disabledReason === null ? () => setConfirming(true) : undefined}
            />
          }
        >
          <Trash2 aria-hidden />
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    </span>
  );
}

interface VersionDetailProps {
  info: VersionInfo;
  projectName: string;
  resourceType: VersionTimeMachineProps["resourceType"];
  resourceId: string;
  readOnly: boolean;
  busy: boolean;
  writing: boolean;
  restoringVersion: number | null;
  onRestore: (version: number) => void;
}

/** 选中版本的预览卡片：媒体、生成说明，以及切换为当前版本的入口。 */
function VersionDetail({
  info,
  projectName,
  resourceType,
  resourceId,
  readOnly,
  busy,
  writing,
  restoringVersion,
  onRestore,
}: VersionDetailProps) {
  const { t } = useTranslation("dashboard");
  const busyHintId = useId();
  return (
    <div className="flex flex-col gap-2 rounded-md bg-muted/50 p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="num text-sm font-medium">v{info.version}</span>
          {info.source === "image_edit" && (
            <Badge variant="outline">{t("version_image_edit_badge")}</Badge>
          )}
          <span className="num text-xs text-subtle-foreground">{info.created_at}</span>
        </span>
        {info.is_current ? (
          <Badge variant="secondary">{t("current_version_badge")}</Badge>
        ) : !readOnly && info.restorable !== false ? (
          <Button
            size="xs"
            disabled={writing || busy}
            aria-describedby={busy ? busyHintId : undefined}
            onClick={() => onRestore(info.version)}
          >
            {restoringVersion === info.version ? t("switching_version") : t("switch_to_version")}
          </Button>
        ) : null}
      </div>
      {busy && !info.is_current && !readOnly && info.restorable !== false ? (
        <p id={busyHintId} className="text-xs text-subtle-foreground">
          {t("version_restore_busy_hint")}
        </p>
      ) : null}

      {info.file_url &&
        (resourceType === "videos" || resourceType === "reference_videos" ? (
          <div className="aspect-video w-full overflow-hidden rounded-md bg-black">
            {info.presentation_available !== true ? (
              // eslint-disable-next-line jsx-a11y/media-has-caption -- 无法进入共享成片读取器的历史视频仅展示原始媒体
              <video
                src={info.file_url}
                aria-label={t("version_preview_alt", { version: info.version })}
                className="size-full object-contain"
                controls
                playsInline
                preload="none"
              />
            ) : (
              <PresentationPlayer
                key={`${resourceType}:${resourceId}:${info.version}`}
                projectName={projectName}
                resourceType={resourceType}
                resourceId={resourceId}
                videoVersion={info.version}
              />
            )}
          </div>
        ) : resourceType === "audio" ? (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- 历史旁白的文字记录显示在同一预览卡片
          <audio
            src={info.file_url}
            aria-label={t("version_audio_preview_label", { version: info.version })}
            className="h-9 w-full"
            controls
            preload="metadata"
          />
        ) : (
          <div
            className={cn(
              "flex w-full items-center justify-center rounded-md bg-background p-2",
              getImagePreviewHeightClass(resourceType),
            )}
          >
            <img
              src={info.file_url}
              alt={t("version_preview_alt", { version: info.version })}
              className="max-h-full w-full object-contain"
            />
          </div>
        ))}

      {resourceType === "audio" && info.file_url && (
        <a
          href={info.file_url}
          download
          className={cn(buttonVariants({ variant: "outline", size: "xs" }), "self-start")}
        >
          <Download aria-hidden data-icon="inline-start" />
          {t("version_download_audio")}
        </a>
      )}

      <p className="line-clamp-4 text-xs text-subtle-foreground">
        {info.prompt ||
          (info.source === "manual_upload"
            ? t("version_manual_upload")
            : t("version_no_notes"))}
      </p>
    </div>
  );
}

/**
 * 资源的历史版本：触发按钮打开弹层，列出版本号，选中一个即预览媒体与生成说明，
 * 非当前版本可切换为当前或移入项目回收站。弹层随触发按钮定位，滚动时跟随，不会自行关闭。
 */
export function VersionTimeMachine({
  projectName,
  resourceType,
  resourceId,
  onRestore,
  iconOnly = false,
  readOnly = false,
  busy = false,
  onRestoringChange,
  checkBusy,
  open: controlledOpen,
  onOpenChange,
  anchor,
}: VersionTimeMachineProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const track = useTrackWrite();
  const titleId = useId();
  const resourcePath =
    resourceType === "storyboards" ? `storyboards/scene_${resourceId}.png` :
    resourceType === "videos" ? `videos/scene_${resourceId}.mp4` :
    resourceType === "reference_videos" ? `reference_videos/${resourceId}.mp4` :
    resourceType === "audio" ? `audio/segment_${resourceId}.wav` :
    resourceType === "characters" ? `characters/${resourceId}.png` :
    // 衍生的 resource id 本身是 `本体/衍生`，两段原样成为路径层级。
    resourceType === "character_derivatives" ? `characters/derivatives/${resourceId}.png` :
    resourceType === "scenes" ? `scenes/${resourceId}.png` :
    resourceType === "grids" ? `grids/${resourceId}.png` :
    resourceType === "products" ? `products/${resourceId}.png` :
    `props/${resourceId}.png`;
  const resourceFp = useProjectsStore((s) => s.getAssetFingerprint(resourcePath));
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const controlled = controlledOpen !== undefined;
  const open = controlled ? controlledOpen : uncontrolledOpen;

  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [currentVersion, setCurrentVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const [selection, setSelection] = useState<VersionSelection | null>(null);
  const selectedVersion =
    selection?.projectName === projectName &&
    selection.resourcePath === resourcePath &&
    selection.fingerprint === resourceFp
      ? selection.version
      : null;
  const [restoringVersion, setRestoringVersion] = useState<number | null>(null);
  const [deletingVersion, setDeletingVersion] = useState<number | null>(null);
  const writing = restoringVersion !== null || deletingVersion !== null;
  const currentPillRef = useRef<HTMLButtonElement>(null);
  const focusCurrentAfterDeleteRef = useRef(false);
  // 删除失败后的补拉不切加载态：列表整块换成加载文字会丢掉键盘焦点
  const silentReloadRef = useRef(false);
  // 面板会随选中项复用：删除返回时资源可能已切换，结果不能落到另一份资源的列表上
  const resourceKey = `${projectName}:${resourcePath}`;
  const resourceKeyRef = useRef(resourceKey);
  useEffect(() => {
    resourceKeyRef.current = resourceKey;
  }, [resourceKey]);
  const setOpen = (next: boolean) => {
    if (!next && writing) return;
    if (controlled) onOpenChange?.(next);
    else setUncontrolledOpen(next);
  };

  const loadVersions = useCallback(async (signal: AbortSignal) => {
    const silent = silentReloadRef.current;
    silentReloadRef.current = false;
    if (!silent) setLoading(true);
    try {
      const data = await API.getVersions(projectName, resourceType, resourceId, { signal });
      if (signal.aborted) return;
      setVersions(data.versions);
      setCurrentVersion(data.current_version);
    } catch {
      if (!signal.aborted && !silent) setVersions([]);
    } finally {
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- 已在 finally 中复位；被 abort 时由接管的新一轮加载负责复位
      if (!signal.aborted) setLoading(false);
    }
  }, [projectName, resourceType, resourceId]);

  useEffect(() => {
    if (!open || !resourceId) return;
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 打开或资源更新时启动带取消信号的版本加载，并同步展示加载状态
    void loadVersions(controller.signal);
    return () => controller.abort();
  }, [open, resourceId, resourceFp, reload, loadVersions]);

  // 删除成功后焦点所在的按钮随版本移出而消失，交给当前版本（预览也回到它）
  useEffect(() => {
    if (!focusCurrentAfterDeleteRef.current) return;
    focusCurrentAfterDeleteRef.current = false;
    // 面板打开期间焦点可能已被用户移到别处，只接管确实丢失的焦点
    if (document.activeElement === document.body) currentPillRef.current?.focus();
  }, [versions]);

  async function handleRestore(version: number) {
    // disabled 是响应式的 restoringVersion/busy：面板打开期间资源转为占用中时随之更新，
    // 这里兜底防止禁用态生效前的一次点击仍发出恢复请求。
    if (busy || writing) return;
    // 渲染快照之外再做一次新鲜读：状态已变、渲染未到的窗口里按钮仍可点。
    if (checkBusy?.()) {
      useAppStore.getState().pushToast(t("version_restore_busy_hint"), "error");
      return;
    }
    setRestoringVersion(version);
    onRestoringChange?.(true);
    try {
      await track((async () => {
        const result = await API.restoreVersion(projectName, resourceType, resourceId, version);
        if (result.asset_fingerprints) {
          useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
        }
        await onRestore?.(version);
        setReload((current) => current + 1);
        // 切换结果直接体现在「当前」标记与媒体上，成功时不弹提示；恢复已换了指纹，按最新指纹记选中
        setSelection({
          projectName,
          resourcePath,
          fingerprint: useProjectsStore.getState().getAssetFingerprint(resourcePath),
          version,
        });
      })());
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("switch_version_failed", { message: errMsg(err) }), "error");
    } finally {
      setRestoringVersion(null);
      onRestoringChange?.(false);
    }
  }

  async function handleDelete(info: VersionInfo) {
    const path = info.file;
    // 占用期间不删：生成结算失败时要回退到上一个当前版本，版本记录被删后回退无处可去
    if (!path || busy || writing) return;
    if (checkBusy?.()) {
      useAppStore.getState().pushToast(t("version_delete_busy_hint"), "error");
      return;
    }
    const { version } = info;
    const startedFor = resourceKey;
    setDeletingVersion(version);
    onRestoringChange?.(true);
    try {
      await track((async () => {
        const { record } = await API.deleteFileViewEntry(projectName, path);
        useAppStore.getState().pushToast(t("version_moved_to_trash", { version }), "info", {
          action: {
            label: t("trash_restore"),
            onClick: () => {
              API.restoreTrashRecord(projectName, record.id)
                .then(() => setReload((current) => current + 1))
                .catch((error: unknown) => {
                  useAppStore.getState().pushToast(t("trash_action_failed", { message: errMsg(error) }), "error");
                })
                .finally(() => useAppStore.getState().invalidateTrash());
            },
          },
        });
        if (resourceKeyRef.current !== startedFor) return;
        focusCurrentAfterDeleteRef.current = true;
        setVersions((current) => current.filter((v) => v.version !== version));
        setSelection((current) =>
          current?.projectName === projectName && current.resourcePath === resourcePath && current.version === version
            ? currentVersion > 0 ? { ...current, version: currentVersion } : null
            : current,
        );
      })());
    } catch (err) {
      useAppStore.getState().pushToast(t("version_delete_failed", { version, message: errMsg(err) }), "error");
      // 失败多半因为列表已过期（别处切换了当前版本或已删除）
      if (resourceKeyRef.current === startedFor) {
        silentReloadRef.current = true;
        setReload((current) => current + 1);
      }
    } finally {
      setDeletingVersion(null);
      onRestoringChange?.(false);
      useAppStore.getState().invalidateTrash();
    }
  }

  if (!resourceId) return null;

  // Derive the selected version's full info from the latest `versions` array
  const selectedInfo =
    selectedVersion != null
      ? versions.find((v) => v.version === selectedVersion) ?? null
      : null;
  const label = t("version_mgmt");

  const trigger = controlled ? null : iconOnly ? (
    <Tooltip>
      <TooltipTrigger
        render={<PopoverTrigger render={<Button variant="ghost" size="icon-sm" aria-label={label} />} />}
      >
        <History aria-hidden />
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  ) : (
    <PopoverTrigger render={<Button variant="ghost" size="xs" className="group/version" />}>
      <History aria-hidden data-icon="inline-start" />
      {label}
      <ChevronDown
        aria-hidden
        data-icon="inline-end"
        className="transition-transform duration-fast group-aria-expanded/version:rotate-180"
      />
    </PopoverTrigger>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {trigger}
      <PopoverContent align="end" anchor={anchor} finalFocus={anchor}>
        <div className="flex items-center justify-between gap-2">
          <PopoverTitle id={titleId}>{t("history_versions")}</PopoverTitle>
          {currentVersion > 0 && (
            <Badge variant="secondary">
              <span className="num">{t("current_version", { version: currentVersion })}</span>
            </Badge>
          )}
        </div>

        {loading ? (
          <p className="text-xs text-subtle-foreground">{t("common:loading")}</p>
        ) : versions.length === 0 ? (
          <div className="flex flex-col gap-1">
            <p className="text-sm">{t("no_history")}</p>
            <p className="text-xs text-subtle-foreground">{t("history_hint")}</p>
          </div>
        ) : (
          <>
            <ToggleGroup
              aria-labelledby={titleId}
              variant="outline"
              size="sm"
              className="flex-wrap"
              value={selectedVersion != null ? [String(selectedVersion)] : []}
              onValueChange={(next: string[]) =>
                setSelection(next[0] ? { projectName, resourcePath, fingerprint: resourceFp, version: Number(next[0]) } : null)
              }
            >
              {versions.map((v) => (
                <div key={`${resourceKey}:${v.version}`} className="flex flex-wrap items-center">
                  <ToggleGroupItem ref={v.is_current ? currentPillRef : undefined} value={String(v.version)}>
                    <span className="num">v{v.version}</span>
                    {v.is_current ? <span aria-hidden className="size-1.5 rounded-full bg-primary" /> : null}
                  </ToggleGroupItem>
                  {!readOnly && (v.is_current || v.file) ? (
                    <VersionDeleteButton
                      version={v.version}
                      disabledReason={
                        v.is_current ? t("version_delete_current_hint") : busy ? t("version_delete_busy_hint") : null
                      }
                      blocked={writing}
                      deleting={deletingVersion === v.version}
                      onConfirm={() => handleDelete(v)}
                    />
                  ) : null}
                </div>
              ))}
            </ToggleGroup>

            {selectedInfo ? (
              <VersionDetail
                info={selectedInfo}
                projectName={projectName}
                resourceType={resourceType}
                resourceId={resourceId}
                readOnly={readOnly}
                busy={busy}
                writing={writing}
                restoringVersion={restoringVersion}
                onRestore={(version) => void handleRestore(version)}
              />
            ) : (
              <p className="text-xs text-subtle-foreground">{t("version_click_hint")}</p>
            )}
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
