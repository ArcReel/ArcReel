import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
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
 * 覆盖已有正式脚本前的 danger 确认（内容确认、广告/短片整份重做）：呈现服务端生成的丢失清单文本，
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
    <ConfirmDialog
      open={open}
      tone="danger"
      title={title ?? t("review_overwrite_title")}
      confirmLabel={confirmLabel ?? t("review_overwrite_confirm")}
      loadingLabel={loadingLabel ?? t("review_confirming")}
      loading={loading}
      confirmDisabled={confirmDisabled}
      onConfirm={onConfirm}
      onCancel={onCancel}
      description={<p className="whitespace-pre-line break-words">{itemIdsInEpisodeText(overwrite.text)}</p>}
    />
  );
}
