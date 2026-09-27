import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Eye, RefreshCw } from "lucide-react";
import { CopyButton } from "@/components/ui/CopyButton";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { errMsg } from "@/utils/async";
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
}: PromptPreviewButtonProps<T>) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  // 接管方轮换 controller：新一轮加载先作废上一轮，被作废方不再回写共享状态。
  const inflight = useRef<AbortController | null>(null);
  const fetchPreview = useCallback(async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    const { signal } = controller;
    setLoading(true);
    setError(null);
    try {
      const next = await loadRef.current(signal);
      if (signal.aborted || inflight.current !== controller) return;
      setResult(next);
    } catch (e) {
      if (signal.aborted || inflight.current !== controller) return;
      setError(errMsg(e));
    } finally {
      if (!signal.aborted && inflight.current === controller) setLoading(false);
    }
  }, []);

  // 卸载时作废在途请求：清理函数不写 state，只切断被接管方的回写。
  useEffect(() => () => inflight.current?.abort(), []);

  const handleOpen = () => {
    setOpen(true);
    void fetchPreview();
  };

  // 每次打开都重新取：关闭时丢弃结果，免得下次打开先闪出过期的文本。
  const handleClose = () => {
    inflight.current?.abort();
    inflight.current = null;
    setOpen(false);
    setLoading(false);
    setResult(null);
    setError(null);
  };

  return (
    <>
      <button
        type="button"
        onClick={handleOpen}
        disabled={disabled}
        title={title}
        className="focus-ring inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors hover:bg-white/5 disabled:cursor-default disabled:opacity-40"
        style={{ color: "var(--color-text-3)" }}
      >
        <Eye aria-hidden className="h-3 w-3" />
        {t("prompt_preview_open")}
      </button>
      <GlassModal open={open} onClose={handleClose} labelledBy={titleId} widthClassName="w-full max-w-2xl">
        <div
          className="flex items-start justify-between gap-4 px-5 py-4"
          style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
        >
          <div className="min-w-0">
            <h2
              id={titleId}
              className="text-[14px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {title}
            </h2>
            {notice ? (
              <p className="mt-1 text-[11px] leading-[1.5]" style={{ color: "var(--color-text-4)" }}>
                {notice}
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {result?.text ? <CopyButton text={result.text} label={t("prompt_preview_copy")} /> : null}
            <button
              type="button"
              onClick={() => void fetchPreview()}
              disabled={loading}
              title={t("prompt_preview_refresh")}
              aria-label={t("prompt_preview_refresh")}
              className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10 disabled:opacity-40"
              style={{ color: "var(--color-text-3)" }}
            >
              <RefreshCw aria-hidden className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
            <ModalCloseButton onClick={handleClose} />
          </div>
        </div>

        <div className="flex max-h-[65vh] flex-col gap-2 overflow-y-auto px-5 py-4">
          {loading && !result ? (
            <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
              {t("prompt_preview_loading")}
            </p>
          ) : null}
          {error ? (
            <p className="text-[11px]" style={{ color: "var(--color-warm)" }}>
              {error}
            </p>
          ) : null}
          {result?.unavailable ? (
            <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
              {result.unavailable}
            </p>
          ) : null}
          {result?.warnings?.length ? (
            <ul
              aria-label={t("prompt_preview_warnings_label")}
              className="space-y-1 rounded px-2 py-1.5 text-[10.5px]"
              style={{
                background: "oklch(0.35 0.10 70 / 0.10)",
                color: "oklch(0.86 0.09 70)",
                border: "1px solid oklch(0.50 0.12 70 / 0.30)",
              }}
            >
              {result.warnings.map((warning, index) => (
                <li key={`prompt-preview-warning-${index}`}>{warning}</li>
              ))}
            </ul>
          ) : null}
          {result?.text ? (
            <pre
              className="overflow-auto whitespace-pre-wrap break-words rounded-md border p-3 text-[11.5px] leading-relaxed"
              style={{
                borderColor: "var(--color-hairline)",
                background: "var(--color-bg-grad-a)",
                color: "var(--color-text-2)",
                fontFamily: "var(--font-mono)",
              }}
            >
              {result.text}
            </pre>
          ) : null}
          {result && renderExtra ? renderExtra(result) : null}
        </div>
      </GlassModal>
    </>
  );
}
