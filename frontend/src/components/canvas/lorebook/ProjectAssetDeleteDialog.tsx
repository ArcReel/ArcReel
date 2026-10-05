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
  /** 与卡片兄弟控件共享的禁用态（生成中 / 上传中 / 版本恢复中）。 */
  busy: boolean;
  /** 删除请求在途与否，供卡片同步禁用兄弟控件。 */
  onDeletingChange: (deleting: boolean) => void;
  /** 「改为并入…」：有引用时提供，转入合并流程。不可合并的类型不传。 */
  onMergeInstead?: () => void;
}

type PreviewState =
  | { phase: "loading" }
  | { phase: "ready"; result: AssetDeletionPreview }
  | { phase: "failed"; message: string };

/**
 * 删除项目资产的确认框。打开时先以 dry_run 查询脚本里的引用：有引用时写明被哪些集引用、删除后
 * 这些分镜在生成时会被拦下，并提供「改为并入…」；没有引用时只写明不可恢复。删除不改写引用，
 * 也不因有引用而禁止。查询请求随关闭或卸载经 AbortSignal 作废；只有查询就绪后才能确认。
 *
 * 离开拦截包住确认动作，不包住打开确认框：取消删除时未保存修改原样保留。
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

  // 每次打开都重新查询，并清掉上一次的错误。
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

  const executeDelete = async () => {
    if (busy || deleting) return;
    if (rejectIfAssetBusy(assetType, projectName, name, t, "assets:gallery_busy_hint")) return;
    updateDeleting(true);
    setError(null);
    try {
      await API.deleteProjectAsset(projectName, assetType, name);
      onOpenChange(false);
      // 删除已提交，刷新是独立的后续步骤：卡片随刷新消失，刷新失败时它会停在页面上。
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed") {
        useAppStore.getState().pushToast(t("assets:gallery_delete_refresh_failed"), "warning");
      }
    } catch (err) {
      setError(t("assets:delete_failed", { message: errMsg(err) }));
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
    impact = t("assets:gallery_delete_irreversible");
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
        // 删除在途时不响应 Esc，避免请求还在途时对话框先消失
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
            onClick={() => confirmLeave(() => void executeDelete())}
          >
            {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("assets:delete")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
