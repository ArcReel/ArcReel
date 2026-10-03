import { useState } from "react";
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
import { errMsg } from "@/utils/async";

export interface MemoryConfirmRequest {
  title: string;
  description: string;
  confirmLabel: string;
  /** 抛错即失败：对话框保持打开并显示错误。 */
  run: () => Promise<void>;
}

/** 删除记忆文件、清空记忆目录的确认：两者都不可恢复。`request` 为 null 时关闭。 */
export function MemoryConfirmDialog({
  request,
  onClose,
}: {
  request: MemoryConfirmRequest | null;
  onClose: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 关闭动画期间沿用上一次的内容；换了请求时清掉上一次的错误
  const [shown, setShown] = useState(request);
  if (request !== null && request !== shown) {
    setShown(request);
    setError(null);
  }

  const confirm = async () => {
    if (!shown || running) return;
    setRunning(true);
    setError(null);
    try {
      await shown.run();
      onClose();
    } catch (err) {
      setError(t("agent_memory_action_failed", { message: errMsg(err) }));
    } finally {
      setRunning(false);
    }
  };

  return (
    <AlertDialog
      open={request !== null}
      onOpenChange={(next) => {
        // 提交中不响应 Esc，避免请求还在途时对话框先消失
        if (!next && !running) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{shown?.title}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            {/* 说明有两段，描述元素渲染为 div，段落才不会嵌套在 p 里 */}
            <AlertDialogDescription render={<div />}>
              <div className="flex flex-col gap-2">
                <p>{shown?.description}</p>
                <p>{t("agent_memory_session_notice")}</p>
              </div>
            </AlertDialogDescription>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={running}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={running} onClick={() => void confirm()}>
            {running && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {shown?.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
