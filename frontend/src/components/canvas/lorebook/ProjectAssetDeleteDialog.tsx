import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API, type AssetDeletionPreview } from "@/api";
import { useConfirmLeave } from "@/components/shared/edit-unit/LeaveGuard";
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
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { AssetSheetType } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeDisplayName, type EpisodeLedger } from "@/utils/episode-display";
import { rejectIfAssetBusy } from "./assetBusyGuard";

interface ProjectAssetDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  assetType: AssetSheetType;
  name: string;
  /** Disabled state shared by sibling card controls (generating/uploading/version restoration). */
  busy: boolean;
  /** Deletion-request occupancy lets cards disable siblings together. */
  onDeletingChange: (deleting: boolean) => void;
  /** Switch to merge when references exist; omit for unmergeable types. */
  onMergeInstead?: () => void;
}

type PreviewState =
  | { phase: "loading" }
  | { phase: "ready"; result: AssetDeletionPreview }
  | { phase: "failed"; message: string };

/**
 * Asset-delete confirmation queries script references with dry_run on opening. Referenced assets list episodes and explain generation
 * will block these shots afterward, offering merge instead; unreferenced assets state irreversibility only. Deletion neither rewrites
 * references nor forbids referenced assets. Close/unmount abort queries via AbortSignal; confirm only when ready.
 *
 * Leave guards wrap confirmation, not dialog opening: canceled deletion preserves unsaved edits. Deletion returns success;
 * request failures or submit-time occupancy also preserve edits through leave guards.
 */
export function ProjectAssetDeleteDialog({
  open,
  onOpenChange,
  projectName,
  assetType,
  name,
  busy,
  onDeletingChange,
  onMergeInstead,
}: ProjectAssetDeleteDialogProps) {
  const { t } = useTranslation(["assets", "common"]);
  const confirmLeave = useConfirmLeave();
  const episodes: EpisodeLedger = useProjectsStore((s) => s.currentProjectData?.episodes) ?? [];
  const [preview, setPreview] = useState<PreviewState>({ phase: "loading" });
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Refetch and clear previous errors on every opening.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setPreview({ phase: "loading" });
      setError(null);
    }
  }

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    API.previewProjectAssetDeletion(projectName, assetType, name, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setPreview({ phase: "ready", result });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setPreview({ phase: "failed", message: errMsg(err) });
      });
    return () => controller.abort();
  }, [open, projectName, assetType, name]);

  const updateDeleting = (next: boolean) => {
    setDeleting(next);
    onDeletingChange(next);
  };

  const executeDelete = async (): Promise<boolean> => {
    if (busy || deleting) return false;
    if (rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint")) return false;
    updateDeleting(true);
    setError(null);
    try {
      await API.deleteProjectAsset(projectName, assetType, name);
      onOpenChange(false);
      // Deletion committed; refresh is separate. Cards disappear on refresh, remaining on-page if refresh fails.
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed") {
        useAppStore.getState().pushToast(t("assets:gallery_delete_refresh_failed"), "warning");
      }
      return true;
    } catch (err) {
      setError(t("assets:delete_failed", { message: errMsg(err) }));
      return false;
    } finally {
      updateDeleting(false);
    }
  };

  const title = t(`assets:gallery_delete_title.${assetType}`, { name });
  const references = preview.phase === "ready" ? preview.result.references : 0;
  const referencedEpisodes = preview.phase === "ready" ? preview.result.episodes : [];

  let impact: string;
  if (preview.phase === "loading") {
    impact = t("assets:gallery_delete_checking");
  } else if (preview.phase === "failed") {
    impact = t("assets:gallery_delete_check_failed", { message: preview.message });
  } else if (references === 0) {
    impact = t("assets:gallery_delete_moves_to_trash");
  } else if (referencedEpisodes.length === 0) {
    impact = t("assets:gallery_delete_impact_unattributed", { count: references });
  } else {
    const first = episodeDisplayName(episodes, referencedEpisodes[0].episode, t);
    impact =
      referencedEpisodes.length === 1
        ? t("assets:gallery_delete_impact_single", { count: references, episode: first })
        : t("assets:gallery_delete_impact_many", {
            count: references,
            episode: first,
            episodes: referencedEpisodes.length,
          });
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // Ignore Esc during deletion so dialogs remain until requests finish.
        if (!next && !deleting) onOpenChange(false);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>
              {preview.phase === "failed" ? <span className="text-warn">{impact}</span> : impact}
            </AlertDialogDescription>
            {referencedEpisodes.length > 1 && (
              <ul className="flex flex-col gap-1">
                {referencedEpisodes.map((item) => (
                  <li key={item.episode} className="flex gap-2">
                    <TruncatedText
                      text={episodeDisplayName(episodes, item.episode, t)}
                      className="min-w-0 flex-1 text-subtle-foreground"
                    />
                    <span className="shrink-0 tabular-nums">
                      {t("assets:gallery_delete_episode_count", { count: item.references })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {error && (
              <p role="alert" className="text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
          {onMergeInstead && references > 0 && (
            <Button variant="outline" disabled={deleting} onClick={onMergeInstead}>
              {t("assets:gallery_delete_merge_instead")}
            </Button>
          )}
          <AlertDialogAction
            variant="destructive"
            disabled={deleting || busy || preview.phase !== "ready"}
            onClick={() => confirmLeave(executeDelete)}
          >
            {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("assets:delete")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
