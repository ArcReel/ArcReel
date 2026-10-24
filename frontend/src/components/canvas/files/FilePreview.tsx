import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import type { FileViewEntry } from "@/types";
import { errMsg } from "@/utils/async";

import { TEXT_PREVIEW_MAX_BYTES, previewKind } from "./file-view-model";

type PreviewState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "media"; url: string }
  | { status: "text"; text: string };

function prettyText(text: string, name: string): string {
  if (!name.toLowerCase().endsWith(".json")) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/** 选中文件的预览：图片、视频、音频与文本（含 JSON）经鉴权接口取回内容，其余类型与过大的文本只提供下载。 */
export function FilePreview({ projectName, entry }: { projectName: string; entry: FileViewEntry }) {
  const { t } = useTranslation("dashboard");
  const kind = previewKind(entry.name);
  const tooLarge = kind === "text" && entry.size > TEXT_PREVIEW_MAX_BYTES;
  const [state, setState] = useState<PreviewState>({ status: "loading" });

  useEffect(() => {
    if (!kind || tooLarge) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    API.getFileViewContent(projectName, entry.path, { signal: controller.signal })
      .then(async (blob) => {
        if (kind === "text") {
          const text = await blob.text();
          if (!controller.signal.aborted) setState({ status: "text", text: prettyText(text, entry.name) });
          return;
        }
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ status: "media", url: objectUrl });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setState({ status: "error", message: errMsg(error) });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [projectName, entry.path, entry.name, kind, tooLarge]);

  if (!kind) return <p className="text-sm text-muted-foreground">{t("file_view_preview_unsupported")}</p>;
  if (tooLarge) return <p className="text-sm text-muted-foreground">{t("file_view_preview_too_large")}</p>;
  if (state.status === "loading") return <p className="text-sm text-muted-foreground">{t("file_view_loading")}</p>;
  if (state.status === "error") {
    return <p className="wrap-break-word text-sm text-destructive">{t("file_view_preview_failed", { message: state.message })}</p>;
  }
  const label = t("file_view_preview_label", { name: entry.name });
  if (state.status === "text") {
    return (
      <pre
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- 只读长文本，须能用键盘聚焦后滚动
        tabIndex={0}
        aria-label={label}
        className="focus-ring rounded-md border border-border bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap wrap-break-word"
      >
        {state.text}
      </pre>
    );
  }
  if (kind === "image") return <img src={state.url} alt={label} className="max-h-120 max-w-full rounded-md border border-border object-contain" />;
  if (kind === "video") {
    // eslint-disable-next-line jsx-a11y/media-has-caption -- 项目里的生成视频没有字幕轨，预览只用于确认内容
    return <video src={state.url} controls aria-label={label} className="max-h-120 max-w-full rounded-md border border-border" />;
  }
  // eslint-disable-next-line jsx-a11y/media-has-caption -- 同上：音频预览没有字幕轨
  return <audio src={state.url} controls aria-label={label} className="w-full" />;
}
