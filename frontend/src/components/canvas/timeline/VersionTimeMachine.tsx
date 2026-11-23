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
  iconOnly?: boolean;
  readOnly?: boolean;
  busy?: boolean;
  onRestoringChange?: (restoring: boolean) => void;
  checkBusy?: () => boolean;
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

interface VersionSelection {
  projectName: string;
  resourcePath: string;
  fingerprint: number | null;
  version: number;
}

interface VersionDeleteButtonProps {
  version: number;
  disabledReason: string | null;
  blocked: boolean;
  deleting: boolean;
  onConfirm: () => Promise<void>;
}

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
        disabled={deleting || blocked || disabledReason !== null}
        onBlur={(event) => dismissOnFocusLeave(event, dismiss)}
        onKeyDown={(event) => dismissOnEscape(event, dismiss)}
        onClick={(event) => {
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
  const silentReloadRef = useRef(false);
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

  useEffect(() => {
    if (!focusCurrentAfterDeleteRef.current) return;
    focusCurrentAfterDeleteRef.current = false;
    if (document.activeElement === document.body) currentPillRef.current?.focus();
  }, [versions]);

  async function handleRestore(version: number) {
    if (busy || writing) return;
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
