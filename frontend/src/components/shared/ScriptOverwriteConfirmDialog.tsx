import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
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
import type { ScriptOverwrite } from "@/types";
import { itemIdsInEpisodeText } from "@/utils/episode-display";

interface ScriptOverwriteConfirmDialogProps {
  open: boolean;
  overwrite: ScriptOverwrite;
  loading: boolean;
  /** 确认前置条件未满足（如视频模型无法解析）时禁用框内确认按钮。 */
  confirmDisabled?: boolean;
  /** 标题与确认按钮文字；缺省为内容确认的「覆盖并确认」。 */
  title?: string;
  confirmLabel?: string;
  loadingLabel?: string;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/**
 * 覆盖已有正式脚本前的不可逆确认（内容确认、广告/短片整份重做）：呈现服务端生成的丢失清单文本，
 * 只把条目 ID 改为集内部分，统计口径与 Agent 回执一致。
 */
export function ScriptOverwriteConfirmDialog({
  open,
  overwrite,
  loading,
  confirmDisabled = false,
  title,
  confirmLabel,
  loadingLabel,
  onConfirm,
  onCancel,
}: ScriptOverwriteConfirmDialogProps) {
  const { t } = useTranslation("dashboard");

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!next && !loading) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{title ?? t("review_overwrite_title")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <AlertDialogDescription>
            <span className="whitespace-pre-line wrap-break-word">{itemIdsInEpisodeText(overwrite.text)}</span>
          </AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={loading || confirmDisabled}
            onClick={() => void onConfirm()}
          >
            {loading ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {loading ? (loadingLabel ?? t("review_confirming")) : (confirmLabel ?? t("review_overwrite_confirm"))}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
