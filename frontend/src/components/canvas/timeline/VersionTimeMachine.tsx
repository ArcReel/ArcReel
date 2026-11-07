import { useCallback, useEffect, useId, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Download, History } from "lucide-react";
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
   * Resource generation/edit occupancy, including optimistic image_edit, disables version restoration.
   * image_edit unconditionally overwrites current on completion; restoring during occupancy could
   * show success then be overwritten, losing users' last choices.
   */
  busy?: boolean;
  /**
   * Report restoration occupancy to parents. `busy` only enforces external-occupancy → no-restore; siblings
   * (generation/upload) need the reverse signal because restoration writes the same file. Otherwise before return
   * both requests could write concurrently, later completion overwriting earlier while both report success.
   */
  onRestoringChange?: (restoring: boolean) => void;
  /**
   * Fresh submit-time recheck: `busy` is a render snapshot; Agents/batches/polling may occupy resources
   * while version panels stay open. Before new props reach buttons, clicks could
   * still request restoration and race writes. Return true to reject restoration.
   */
  checkBusy?: () => boolean;
  /**
   * Controlled opening: external entries such as card more menus decide open/close, without an internal trigger button.
   * Position popovers by `anchor`, restoring focus there on close.
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

// Key preview selections by owning resource/version fingerprint, never reusing another resource's selection after changes.
interface VersionSelection {
  projectName: string;
  resourcePath: string;
  fingerprint: number | null;
  version: number;
}

/**
 * Resource version history: trigger opens version-list popovers; selection previews media/generation details,
 * noncurrent versions can become current. Anchor-following popovers track scrolling without closing.
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
  const busyHintId = useId();
  const resourcePath =
    resourceType === "storyboards" ? `storyboards/scene_${resourceId}.png` :
    resourceType === "videos" ? `videos/scene_${resourceId}.mp4` :
    resourceType === "reference_videos" ? `reference_videos/${resourceId}.mp4` :
    resourceType === "audio" ? `audio/segment_${resourceId}.wav` :
    resourceType === "characters" ? `characters/${resourceId}.png` :
    // Derivative resource IDs use `本体/衍生`, both segments preserved as path hierarchy.
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
  const setOpen = (next: boolean) => {
    if (!next && restoringVersion !== null) return;
    if (controlled) onOpenChange?.(next);
    else setUncontrolledOpen(next);
  };

  const loadVersions = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    try {
      const data = await API.getVersions(projectName, resourceType, resourceId, { signal });
      if (signal.aborted) return;
      setVersions(data.versions);
      setCurrentVersion(data.current_version);
    } catch {
      if (!signal.aborted) setVersions([]);
    } finally {
      // react-doctor-disable-next-line react-doctor/no-loading-flag-reset-outside-finally -- Already reset in finally; abort hands reset responsibility to the replacing load
      if (!signal.aborted) setLoading(false);
    }
  }, [projectName, resourceType, resourceId]);

  useEffect(() => {
    if (!open || !resourceId) return;
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Opening/resource updates start cancelable version loads and synchronously show loading
    void loadVersions(controller.signal);
    return () => controller.abort();
  }, [open, resourceId, resourceFp, reload, loadVersions]);


  async function handleRestore(version: number) {
    // disabled reacts to restoringVersion/busy and updates if resources become occupied while panels remain open;
    // guard clicks before disabled state reaches buttons too.
    if (busy || restoringVersion !== null) return;
    // Fresh-read beyond render snapshots: buttons may still accept clicks after state changes but before rerender.
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
        // Switch results appear in current markers/media without success toasts; restored fingerprints change, so record selection under latest fingerprints.
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
                <ToggleGroupItem key={v.version} value={String(v.version)}>
                  <span className="num">v{v.version}</span>
                  {v.is_current ? <span aria-hidden className="size-1.5 rounded-full bg-primary" /> : null}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>

            {selectedInfo ? (
              <div className="flex flex-col gap-2 rounded-md bg-muted/50 p-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                    <span className="num text-sm font-medium">v{selectedInfo.version}</span>
                    {selectedInfo.source === "image_edit" && (
                      <Badge variant="outline">{t("version_image_edit_badge")}</Badge>
                    )}
                    <span className="num text-xs text-subtle-foreground">{selectedInfo.created_at}</span>
                  </span>
                  {selectedInfo.is_current ? (
                    <Badge variant="secondary">{t("current_version_badge")}</Badge>
                  ) : !readOnly && selectedInfo.restorable !== false ? (
                    <Button
                      size="xs"
                      disabled={restoringVersion !== null || busy}
                      aria-describedby={busy ? busyHintId : undefined}
                      onClick={() => void handleRestore(selectedInfo.version)}
                    >
                      {restoringVersion === selectedInfo.version ? t("switching_version") : t("switch_to_version")}
                    </Button>
                  ) : null}
                </div>
                {busy && !selectedInfo.is_current && !readOnly && selectedInfo.restorable !== false ? (
                  <p id={busyHintId} className="text-xs text-subtle-foreground">
                    {t("version_restore_busy_hint")}
                  </p>
                ) : null}

                {selectedInfo.file_url &&
                  (resourceType === "videos" || resourceType === "reference_videos" ? (
                    <div className="aspect-video w-full overflow-hidden rounded-md bg-black">
                      {selectedInfo.presentation_available !== true ? (
                        // eslint-disable-next-line jsx-a11y/media-has-caption -- Historical videos outside the shared finished-media reader show original media only
                        <video
                          src={selectedInfo.file_url}
                          aria-label={t("version_preview_alt", { version: selectedInfo.version })}
                          className="size-full object-contain"
                          controls
                          playsInline
                          preload="none"
                        />
                      ) : (
                        <PresentationPlayer
                          key={`${resourceType}:${resourceId}:${selectedInfo.version}`}
                          projectName={projectName}
                          resourceType={resourceType}
                          resourceId={resourceId}
                          videoVersion={selectedInfo.version}
                        />
                      )}
                    </div>
                  ) : resourceType === "audio" ? (
                    // eslint-disable-next-line jsx-a11y/media-has-caption -- Historical narration text appears in the same preview card
                    <audio
                      src={selectedInfo.file_url}
                      aria-label={t("version_audio_preview_label", { version: selectedInfo.version })}
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
                        src={selectedInfo.file_url}
                        alt={t("version_preview_alt", { version: selectedInfo.version })}
                        className="max-h-full w-full object-contain"
                      />
                    </div>
                  ))}

                {resourceType === "audio" && selectedInfo.file_url && (
                  <a
                    href={selectedInfo.file_url}
                    download
                    className={cn(buttonVariants({ variant: "outline", size: "xs" }), "self-start")}
                  >
                    <Download aria-hidden data-icon="inline-start" />
                    {t("version_download_audio")}
                  </a>
                )}

                <p className="line-clamp-4 text-xs text-subtle-foreground">
                  {selectedInfo.prompt ||
                    (selectedInfo.source === "manual_upload"
                      ? t("version_manual_upload")
                      : t("version_no_notes"))}
                </p>
              </div>
            ) : (
              <p className="text-xs text-subtle-foreground">{t("version_click_hint")}</p>
            )}
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
