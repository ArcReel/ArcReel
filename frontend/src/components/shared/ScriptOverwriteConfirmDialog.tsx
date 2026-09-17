import { Film, Image as ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { ScriptOverwrite } from "@/types";

interface ScriptOverwriteConfirmDialogProps {
  open: boolean;
  overwrite: ScriptOverwrite;
  loading: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/**
 * 内容确认覆盖已有正式脚本前的 danger 确认：说明确认会整份重建正式脚本，列出将被移除的分镜，
 * 以及随分镜移除、不再显示的分镜图与视频。
 */
export function ScriptOverwriteConfirmDialog({
  open,
  overwrite,
  loading,
  onConfirm,
  onCancel,
}: ScriptOverwriteConfirmDialogProps) {
  const { t } = useTranslation("dashboard");
  const hasAssets = overwrite.storyboard_count > 0 || overwrite.video_count > 0;

  return (
    <ConfirmDialog
      open={open}
      tone="danger"
      title={t("review_overwrite_title")}
      confirmLabel={t("review_overwrite_confirm")}
      loadingLabel={t("review_confirming")}
      loading={loading}
      onConfirm={onConfirm}
      onCancel={onCancel}
      description={
        <div className="flex flex-col gap-2">
          <p>{t("review_overwrite_desc", { count: overwrite.entries.length })}</p>
          {hasAssets && (
            <p>
              {t("review_overwrite_assets", {
                storyboards: overwrite.storyboard_count,
                videos: overwrite.video_count,
              })}
            </p>
          )}
          {overwrite.entries.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] text-text-4">{t("review_overwrite_entries_label")}</p>
              <ul className="flex max-h-40 flex-wrap gap-1 overflow-y-auto" aria-label={t("review_overwrite_entries_label")}>
                {overwrite.entries.map((entry) => (
                  <li
                    key={entry.id}
                    className="inline-flex items-center gap-1 rounded border border-hairline bg-bg-grad-a/50 px-1.5 py-0.5 font-mono text-[10.5px] text-text-2"
                  >
                    {entry.id}
                    {entry.has_storyboard && (
                      <ImageIcon className="h-3 w-3 text-text-4" aria-label={t("review_overwrite_has_storyboard")} />
                    )}
                    {entry.has_video && (
                      <Film className="h-3 w-3 text-text-4" aria-label={t("review_overwrite_has_video")} />
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      }
    />
  );
}
