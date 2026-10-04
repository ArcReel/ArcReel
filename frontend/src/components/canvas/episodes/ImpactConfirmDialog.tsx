import { useState, type ReactNode } from "react";
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

export interface ImpactConfirmRequest {
  title: string;
  /** 正文，通常是服务端成文的受影响集清单。 */
  body: ReactNode;
  confirmLabel: string;
  /** 提交中的按钮文案；缺省沿用 `confirmLabel`。 */
  runningLabel?: string;
  /** 会丢失产物或无法恢复：确认按钮按危险操作显示。 */
  destructive: boolean;
  /** 正文里有输入框等可聚焦元素：正文不再单独进入 Tab 顺序。 */
  interactiveBody?: boolean;
}

/**
 * 「分集」视图里改动分集与源文前的确认：删除一集、手工切分、整本源文文件改动、重新规划与采纳新方案等。
 * `request` 为 null 时关闭；提交中禁用两个按钮并忽略关闭请求。受影响集清单可能很长，正文可以用键盘滚动。
 */
export function ImpactConfirmDialog({
  request,
  busy,
  onConfirm,
  onCancel,
}: {
  request: ImpactConfirmRequest | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("common");
  // 关闭动画期间沿用上一次的内容
  const [shown, setShown] = useState(request);
  if (request !== null && request !== shown) setShown(request);

  return (
    <AlertDialog
      open={request !== null}
      onOpenChange={(next) => {
        if (!next && !busy) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{shown?.title}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody
          {...(shown?.interactiveBody ? {} : { tabIndex: 0, role: "region", "aria-label": shown?.title })}
        >
          <AlertDialogDescription render={<div />}>{shown?.body}</AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant={shown?.destructive ? "destructive" : "default"}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {busy ? (shown?.runningLabel ?? shown?.confirmLabel) : shown?.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** 确认正文里服务端成文的清单：保留换行；清单在确认期间变了时先说明。 */
export function ImpactText({ text, changedNotice }: { text: string; changedNotice?: string | null }) {
  return (
    <div className="flex flex-col gap-2">
      {changedNotice ? <p className="text-warn">{changedNotice}</p> : null}
      <p className="whitespace-pre-line">{text}</p>
    </div>
  );
}
