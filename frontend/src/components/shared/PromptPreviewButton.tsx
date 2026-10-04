import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, Eye, RefreshCw } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { errMsg } from "@/utils/async";
import { copyText } from "@/utils/clipboard";
import type { RenderedPromptPreview } from "@/types";

export interface PromptPreviewButtonProps<T extends RenderedPromptPreview> {
  /** 弹窗标题，同时作为触发按钮的悬停说明，区分同一页面上的多个入口。 */
  title: string;
  /** 取最终提示词：每次打开与刷新各调一次，调用时读到的是调用方最新的闭包（如当前草稿）。 */
  load: (signal: AbortSignal) => Promise<T>;
  /** 标题下的口径说明，如「按已保存内容渲染」或「按当前模型能力计算」。 */
  notice?: ReactNode;
  /** 在最终文本下方追加的区块，拿到的是本次请求的完整结果（如随请求发出的参考图列表）。 */
  renderExtra?: (result: T) => ReactNode;
  disabled?: boolean;
  /** 有未保存修改时先保存；失败则保留原处，不打开预览。回调需用 useCallback。 */
  beforeOpen?: () => Promise<boolean>;
  saveFirst?: boolean;
}

/**
 * 「查看提示词」按钮与最终提示词弹窗：打开才请求，弹窗内可复制与重新渲染。
 *
 * 渲染在后端完成（与执行期同一出口），前端不复刻任何拼接逻辑；预览只读，不触发生成。
 */
export function PromptPreviewButton<T extends RenderedPromptPreview>({
  title,
  load,
  notice,
  renderExtra,
  disabled,
  beforeOpen,
  saveFirst,
}: PromptPreviewButtonProps<T>) {
  const { t } = useTranslation("dashboard");
  const [preparing, setPreparing] = useState(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // 接管方轮换 controller：新一轮加载先作废上一轮，被作废方不再回写共享状态。
  const inflight = useRef<AbortController | null>(null);
  const fetchPreview = useCallback(async (prepared?: AbortController) => {
    if (!prepared) inflight.current?.abort();
    const controller = prepared ?? new AbortController();
    inflight.current = controller;
    const { signal } = controller;
    setLoading(true);
    setError(null);
    try {
      const next = await load(signal);
      if (signal.aborted || inflight.current !== controller) return;
      setResult(next);
    } catch (e) {
      if (signal.aborted || inflight.current !== controller) return;
      setError(errMsg(e));
    } finally {
      if (!signal.aborted && inflight.current === controller) setLoading(false);
    }
  }, [load]);

  // 卸载时作废在途请求：清理函数不写 state，只切断被接管方的回写。
  useEffect(() => () => inflight.current?.abort(), []);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleOpen = async () => {
    if (preparing) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setPreparing(true);
    try {
      if (beforeOpen && !(await beforeOpen())) return;
      if (controller.signal.aborted || inflight.current !== controller) return;
      setOpen(true);
      await fetchPreview(controller);
    } finally {
      if (!controller.signal.aborted && inflight.current === controller) setPreparing(false);
    }
  };

  // 每次打开都重新取：关闭时丢弃结果，免得下次打开先闪出过期的文本。
  const handleClose = () => {
    inflight.current?.abort();
    inflight.current = null;
    setOpen(false);
    setLoading(false);
    setPreparing(false);
    setResult(null);
    setError(null);
    setCopied(false);
  };

  // 复制成功才显示「已复制」：非安全上下文走 execCommand 兜底，兜底也失败时不假报成功
  const handleCopy = (text: string) => {
    void copyText(text).then(
      () => setCopied(true),
      () => undefined,
    );
  };

  return (
    <>
      <Button variant="ghost" size="xs" onClick={() => void handleOpen()} disabled={disabled || preparing} title={title}>
        <Eye aria-hidden data-icon="inline-start" />
        {t(saveFirst ? "prompt_preview_save_first" : "prompt_preview_open")}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) handleClose();
        }}
      >
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {notice ? <DialogDescription>{notice}</DialogDescription> : null}
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-3">
              {loading && !result ? (
                <p className="text-sm text-muted-foreground">{t("prompt_preview_loading")}</p>
              ) : null}
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
              {result?.unavailable ? <p className="text-sm text-muted-foreground">{result.unavailable}</p> : null}
              {result?.warnings?.length ? (
                <ul
                  aria-label={t("prompt_preview_warnings_label")}
                  className="flex flex-col gap-1 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn"
                >
                  {result.warnings.map((warning, index) => (
                    <li key={`prompt-preview-warning-${index}`}>{warning}</li>
                  ))}
                </ul>
              ) : null}
              {result?.text ? (
                <pre className="rounded-md border bg-card p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-subtle-foreground wrap-break-word">
                  {result.text}
                </pre>
              ) : null}
              {result && renderExtra ? renderExtra(result) : null}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => void fetchPreview()} disabled={loading}>
              <RefreshCw aria-hidden data-icon="inline-start" className={cn(loading && "animate-spin")} />
              {t("prompt_preview_refresh")}
            </Button>
            {result?.text ? (
              <Button onClick={() => handleCopy(result.text ?? "")}>
                {copied ? <Check aria-hidden data-icon="inline-start" /> : <Copy aria-hidden data-icon="inline-start" />}
                {copied ? t("message_copied") : t("prompt_preview_copy")}
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
