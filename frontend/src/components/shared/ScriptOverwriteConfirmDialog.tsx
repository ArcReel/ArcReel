import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { ScriptOverwrite } from "@/types";

interface ScriptOverwriteConfirmDialogProps {
  open: boolean;
  overwrite: ScriptOverwrite;
  loading: boolean;
  /** 确认前置条件未满足（如视频模型无法解析）时禁用框内确认按钮。 */
  confirmDisabled?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/**
 * 内容确认覆盖已有正式脚本前的 danger 确认：原样呈现服务端生成的丢失清单文本，
 * Agent 收到的是同一份，措辞与统计口径不在前端另拼。
 */
export function ScriptOverwriteConfirmDialog({
  open,
  overwrite,
  loading,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ScriptOverwriteConfirmDialogProps) {
  const { t } = useTranslation("dashboard");

  return (
    <ConfirmDialog
      open={open}
      tone="danger"
      title={t("review_overwrite_title")}
      confirmLabel={t("review_overwrite_confirm")}
      loadingLabel={t("review_confirming")}
      loading={loading}
      confirmDisabled={confirmDisabled}
      onConfirm={onConfirm}
      onCancel={onCancel}
      description={<p className="whitespace-pre-line break-words">{overwrite.text}</p>}
    />
  );
}
