import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { API, type AssetRenameResult, type ProjectAssetType } from "@/api";
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
import { Input } from "@/components/ui/input";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { useTrackWrite } from "./useAssetWrites";
import { rejectIfAssetBusy, useAssetBusyNames } from "./assetBusyGuard";

interface EditableAssetNameProps {
  projectName: string;
  name: string;
  assetType: ProjectAssetType;
  /**
   * Name heading: `hidden` true while editing still renders it visually hidden, preserving overlay accessible names.
   */
  renderTitle: (hidden: boolean) => ReactNode;
  /** Read-only onboarding demos omit rename entry points. */
  readOnly?: boolean;
  /** Occupancy shared with sibling detail controls: generation/upload/save. */
  busy?: boolean;
  /** Called after rename commits before project refresh, allowing selection to adopt the new name. */
  onRenamed?: (from: string, to: string) => void;
}

/**
 * Inline rename: pencil → name input (Enter submit/Esc cancel) → dry-run impact preview
 * → confirmation → cascading rename. Immediate action outside unsaved edits, without success toasts.
 *
 * Rename cascades across all episode references/related files and is occupancy-aware: entering editing/confirming submission
 * both recheck via `rejectIfAssetBusy`. Success refreshes through the sole `refreshProject` funnel.
 */
export function EditableAssetName({
  projectName,
  name,
  assetType,
  renderTitle,
  readOnly = false,
  busy = false,
  onRenamed,
}: EditableAssetNameProps) {
  const { t } = useTranslation(["assets", "common"]);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState<AssetRenameResult | null>(null);
  const [renaming, setRenaming] = useState(false);
  const track = useTrackWrite();
  const inputRef = useRef<HTMLInputElement>(null);
  // Confirmation performs one-click cascade: occupancy changes disable it immediately; rejectIfBusy still rechecks on submission.
  const queueBusy = useAssetBusyNames(assetType, projectName).has(name);

  useEffect(() => {
    if (!isEditing) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [isEditing]);

  const trimmed = draft.trim();
  const submitting = previewLoading || renaming;
  const canSubmit = trimmed.length > 0 && !submitting;

  if (readOnly) return renderTitle(false);

  /**
   * Three-way occupancy union: `rejectIfAssetBusy` sees queues only, while detail uploads/saves and
   * this component's not-yet-refreshed rename are local state outside queues. Combine `busy` / `renaming` in
   * the same guard. `renaming` lasts until `refreshProject` settles; before then project data still carries old names, so approval would
   * resubmit using stale base names.
   */
  const rejectIfBusy = () => {
    if (busy || renaming) {
      useAppStore.getState().pushToast(t("assets:rename_busy_hint"), "info");
      return true;
    }
    return rejectIfAssetBusy(assetType, projectName, name, t, "assets:rename_busy_hint");
  };

  const enterEdit = () => {
    if (rejectIfBusy()) return;
    setDraft(name);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setDraft(name);
    setIsEditing(false);
  };

  const requestPreview = async () => {
    if (!canSubmit) return;
    if (trimmed === name) {
      setIsEditing(false);
      return;
    }
    if (rejectIfBusy()) return;
    setPreviewLoading(true);
    try {
      const result = await API.renameProjectAsset(projectName, assetType, name, trimmed, { dryRun: true });
      setPreview(result);
    } catch (err) {
      useAppStore.getState().pushToast(t("assets:rename_failed", { message: errMsg(err) }), "error");
    } finally {
      setPreviewLoading(false);
    }
  };

  const executeRename = async () => {
    if (!preview) return;
    if (rejectIfBusy()) {
      setPreview(null);
      return;
    }
    setRenaming(true);
    try {
      await track((async () => {
        const result = await API.renameProjectAsset(projectName, assetType, name, trimmed);
        setPreview(null);
        setIsEditing(false);
        onRenamed?.(name, result.new_name);
        // Rename committed; refresh is separate. refreshProject reports failure through settlement values rather than rejection;
        // without separate feedback details would retain old data, appearing ineffective. cancelled means project navigation, silently.
        const refreshed = await useProjectsStore.getState().refreshProject(projectName);
        if (refreshed === "failed") {
          useAppStore.getState().pushToast(t("assets:rename_refresh_failed"), "warning");
        }
      })());
    } catch (err) {
      useAppStore.getState().pushToast(t("assets:rename_failed", { message: errMsg(err) }), "error");
      // Failure keeps confirmation closed and editing active, allowing corrections/retries.
      setPreview(null);
    } finally {
      setRenaming(false);
    }
  };

  if (!isEditing) {
    return (
      <div className="flex min-w-0 items-center gap-1">
        {renderTitle(false)}
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={enterEdit}
          disabled={busy || renaming}
          aria-label={t("assets:rename_asset")}
        >
          <Pencil aria-hidden />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-1">
      {renderTitle(true)}
      <form
        className="flex min-w-0 flex-1 items-center gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          void requestPreview();
        }}
      >
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Escape" || e.nativeEvent.isComposing) return;
            // Exit rename only; stop Esc bubbling to the containing Sheet.
            e.preventDefault();
            e.stopPropagation();
            cancelEdit();
          }}
          disabled={submitting}
          aria-label={t("assets:rename_asset")}
          className="min-w-0 flex-1"
        />
        <Button type="submit" variant="ghost" size="icon-sm" disabled={!canSubmit} aria-label={t("common:save")}>
          {previewLoading ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />}
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={cancelEdit} disabled={submitting} aria-label={t("common:cancel")}>
          <X aria-hidden />
        </Button>
      </form>
      <AlertDialog
        open={preview !== null}
        onOpenChange={(next) => {
          // Ignore Esc during submission; confirmation remains while requests run.
          if (!next && !renaming) setPreview(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("assets:rename_confirm_title", { name })}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogBody tabIndex={0} role="region" aria-label={t("assets:rename_confirm_title", { name })}>
            <div className="flex flex-col gap-1.5">
              <AlertDialogDescription>
                {preview && (preview.references > 0 || preview.files > 0)
                  ? t("assets:rename_impact", {
                      episodes: preview.episodes,
                      references: preview.references,
                      files: preview.files,
                    })
                  : t("assets:rename_impact_none")}
              </AlertDialogDescription>
              <p className="text-sm text-subtle-foreground">
                {name} → {trimmed}
              </p>
            </div>
          </AlertDialogBody>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={renaming}>{t("common:cancel")}</AlertDialogCancel>
            <AlertDialogAction disabled={renaming || busy || queueBusy} onClick={() => void executeRename()}>
              {renaming ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
              {renaming ? t("assets:renaming") : t("assets:rename_asset")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
