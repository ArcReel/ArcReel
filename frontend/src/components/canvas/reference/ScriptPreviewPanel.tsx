import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { API } from "@/api";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import { assetColor } from "./asset-colors";
import type { MentionLookup } from "@/hooks/useShotPromptHighlight";
import { errMsg } from "@/utils/async";
import type { ScriptPreview } from "@/types";

/** 停止输入到发起解析请求的等待时长（ms）：解析是纯读，节流只为省往返。 */
const DEBOUNCE_MS = 400;

export interface ScriptPreviewPanelProps {
  projectName: string;
  episode: number;
  /** 当前文稿正文（草稿优先），与编辑器同一个值。 */
  text: string;
  /** 资产名 → 类型，供 mention 着色；调用侧须 memo 化。 */
  lookup: MentionLookup;
}

/**
 * 解析预览面板：把编辑器里的文稿按解析器读到的样子摊开。
 *
 * 分工——高亮文稿在本地即时渲染，跟得上打字；派生出的参考图、台词与降级提示由后端
 * 解析接口给出（声音相关的几条依赖项目当前视频模型能力，前端无从判断），停止输入后
 * 才发一次请求。前一次请求随下一次输入作废（AbortSignal），慢响应不会盖住新结果。
 */
export function ScriptPreviewPanel({ projectName, episode, text, lookup }: ScriptPreviewPanelProps) {
  const { t } = useTranslation("dashboard");
  const [preview, setPreview] = useState<ScriptPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setLoading(true);
      API.previewReferenceScript(projectName, episode, text, { signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted) return;
          setPreview(result);
          setError(null);
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return;
          setError(errMsg(e));
        })
        .finally(() => {
          if (controller.signal.aborted) return;
          setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [projectName, episode, text]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const counts = useMemo(() => {
    const utterances = preview?.utterances ?? [];
    return {
      dialogue: utterances.filter((u) => u.kind === "dialogue").length,
      voiceover: utterances.filter((u) => u.kind === "voiceover").length,
    };
  }, [preview]);

  const warnings = preview?.warnings ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
      <div className="mb-2 flex items-center gap-2 text-[11px] text-[var(--color-text-4)]">
        <span>{t("script_preview_hint")}</span>
        <span className="flex-1" />
        {loading && (
          <span role="status" className="inline-flex items-center">
            <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <span className="sr-only">{t("script_preview_loading")}</span>
          </span>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-2 rounded-md bg-red-500/10 px-2.5 py-1.5 text-[11.5px] text-red-300">
          {t("script_preview_failed", { error })}
        </p>
      )}

      {warnings.length > 0 && (
        <ul
          aria-label={t("script_preview_warnings_label")}
          aria-live="polite"
          className="mb-3 flex flex-col gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 p-2"
        >
          {warnings.map((w, i) => (
            <li key={`${w.key}-${i}`} className="flex gap-1.5 text-[11.5px] leading-relaxed text-amber-200">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
              <span>{w.message}</span>
            </li>
          ))}
        </ul>
      )}

      <ScriptHighlight
        text={text}
        lookup={lookup}
        className="rounded-md border border-[var(--color-hairline-soft)] bg-[oklch(0.16_0.010_265_/_0.6)] p-3"
      />

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-2 border-t border-[var(--color-hairline-soft)] pt-3 text-[11.5px]">
        <dt className="text-[var(--color-text-4)]">{t("script_preview_shots")}</dt>
        <dd className="font-mono tabular-nums text-[var(--color-text-2)]">
          {preview?.shots.length ?? 0}
        </dd>

        <dt className="text-[var(--color-text-4)]">{t("script_preview_references")}</dt>
        <dd className="flex flex-wrap gap-1">
          {preview && preview.references.length > 0
            ? preview.references.map((ref, i) => {
                const palette = assetColor(ref.type);
                return (
                  <span
                    key={`${ref.type}:${ref.name}`}
                    translate="no"
                    className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${palette.textClass} ${palette.bgClass} ${palette.borderClass}`}
                  >
                    <span className="font-mono tabular-nums opacity-70">{i + 1}</span>
                    {ref.name}
                  </span>
                );
              })
            : <span className="text-[var(--color-text-4)]">{t("script_preview_none")}</span>}
        </dd>

        <dt className="text-[var(--color-text-4)]">{t("script_preview_utterances")}</dt>
        <dd className="text-[var(--color-text-2)]">
          {counts.dialogue + counts.voiceover > 0
            ? t("script_preview_utterances_value", {
                dialogue: counts.dialogue,
                voiceover: counts.voiceover,
              })
            : <span className="text-[var(--color-text-4)]">{t("script_preview_none")}</span>}
        </dd>
      </dl>
    </div>
  );
}
