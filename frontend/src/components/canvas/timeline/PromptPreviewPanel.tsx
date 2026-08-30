import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, RefreshCw } from "lucide-react";
import { CopyButton } from "@/components/ui/CopyButton";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type { RenderedPromptPreview } from "@/types";

type PromptSide = "storyboard_image" | "video";

interface PromptPreviewPanelProps {
  projectName: string;
  scriptFile: string;
  segmentId: string;
  side: PromptSide;
  /** 条目有未保存改动：预览读的是已保存内容，据此提示口径差异。 */
  dirty: boolean;
}

/**
 * 「预览最终提示词」区域：展开时向后端取该条目这一侧送进模型的最终文本。
 *
 * 渲染在后端完成（与执行期同一出口），前端不复刻任何拼接逻辑；预览只读，不触发生成。
 */
export function PromptPreviewPanel({ projectName, scriptFile, segmentId, side, dirty }: PromptPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rendered, setRendered] = useState<RenderedPromptPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 接管方轮换 controller：新一轮加载先作废上一轮，被作废方不再回写共享状态。
  const inflight = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    const { signal } = controller;
    setLoading(true);
    setError(null);
    try {
      const preview = await API.previewScriptItemPrompts(projectName, segmentId, scriptFile, { signal });
      if (signal.aborted || inflight.current !== controller) return;
      setRendered(preview[side]);
    } catch (e) {
      if (signal.aborted || inflight.current !== controller) return;
      setError(errMsg(e));
    } finally {
      if (!signal.aborted && inflight.current === controller) setLoading(false);
    }
  }, [projectName, scriptFile, segmentId, side]);

  // 换条目即作废在途请求与已取到的结果：留着会把上一条的提示词当成这一条的。
  // 渲染阶段状态同步（React 推荐），免去 effect 的额外渲染周期。
  const itemKey = `${projectName}\u0000${scriptFile}\u0000${segmentId}`;
  const [syncedItemKey, setSyncedItemKey] = useState(itemKey);
  if (syncedItemKey !== itemKey) {
    setSyncedItemKey(itemKey);
    setRendered(null);
    setError(null);
    setLoading(false);
    setOpen(false);
  }

  // 换条目 / 卸载时作废在途请求：清理函数不写 state，只切断被接管方的回写。
  useEffect(() => () => inflight.current?.abort(), [itemKey]);

  const toggleOpen = () => {
    const next = !open;
    setOpen(next);
    if (next && !rendered && !error && !loading) void load();
  };

  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={toggleOpen}
          aria-expanded={open}
          className="focus-ring inline-flex items-center gap-1 rounded text-[10px] text-gray-500 hover:text-gray-400"
        >
          <ChevronDown aria-hidden className={`h-3 w-3 transition-transform ${open ? "" : "-rotate-90"}`} />
          {t("prompt_preview_toggle")}
        </button>
        <span className="flex-1" />
        {open && rendered?.text ? <CopyButton text={rendered.text} label={t("prompt_preview_copy")} /> : null}
        {open && (
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            title={t("prompt_preview_refresh")}
            aria-label={t("prompt_preview_refresh")}
            className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors hover:bg-white/10 disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <RefreshCw aria-hidden className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        )}
      </div>

      {open && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px]" style={{ color: "var(--color-text-4)" }}>
            {dirty ? t("prompt_preview_saved_only_dirty") : t("prompt_preview_saved_only")}
          </p>
          {loading && !rendered ? (
            <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
              {t("prompt_preview_loading")}
            </p>
          ) : null}
          {error ? (
            <p className="text-[11px]" style={{ color: "var(--color-warm)" }}>
              {error}
            </p>
          ) : null}
          {rendered?.unavailable ? (
            <p className="text-[11px]" style={{ color: "var(--color-text-4)" }}>
              {rendered.unavailable}
            </p>
          ) : null}
          {rendered?.text ? (
            <pre
              className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border p-2 text-[11px] leading-relaxed"
              style={{
                borderColor: "var(--color-hairline)",
                background: "var(--color-bg-grad-a)",
                color: "var(--color-text-2)",
                fontFamily: "var(--font-mono)",
              }}
            >
              {rendered.text}
            </pre>
          ) : null}
        </div>
      )}
    </div>
  );
}
