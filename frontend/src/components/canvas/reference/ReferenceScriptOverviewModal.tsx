import { useId } from "react";
import { Clock, Copy, Download, FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { ScriptHighlight } from "@/components/shared/ScriptHighlight";
import type { MentionLookup } from "@/hooks/useUnitPromptHighlight";
import { useAppStore } from "@/stores/app-store";
import { downloadBlob, sanitizeFilename } from "@/utils/download";
import type { ReferenceVideoUnit } from "@/types";

export interface ReferenceScriptOverviewModalProps {
  open: boolean;
  onClose: () => void;
  episodeTitle: string;
  units: ReferenceVideoUnit[];
  lookup: MentionLookup;
}

/** 纯文本导出格式：与 ScriptHighlight 渲染同源的原文，供人工审核/打磨在应用外使用。 */
function formatUnitsAsText(episodeTitle: string, units: ReferenceVideoUnit[]): string {
  const body = units
    .map((u) => `[${u.unit_id}] (${u.duration_seconds}s)\n${u.text}`)
    .join("\n\n");
  return `${episodeTitle}\n\n${body}\n`;
}

/**
 * 整稿预览：把当前集全部视频单元的正文按播放顺序整理在一个可滚动面板里，
 * 供审核/打磨时通读，而不必逐条切换单元卡片；并提供复制全文与下载 .txt。
 *
 * 只读——修改仍走每个单元卡片自己的「脚本」标签，避免这里出现第二个正文真值源。
 */
export function ReferenceScriptOverviewModal({
  open,
  onClose,
  episodeTitle,
  units,
  lookup,
}: ReferenceScriptOverviewModalProps) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const pushToast = useAppStore((s) => s.pushToast);

  const totalSeconds = units.reduce((sum, u) => sum + u.duration_seconds, 0);
  const plainText = formatUnitsAsText(episodeTitle, units);

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(plainText);
      pushToast(t("reference_overview_copy_success"), "success");
    } catch {
      pushToast(t("reference_overview_copy_failed"), "error");
    }
  };

  const handleDownload = () => {
    const blob = new Blob([plainText], { type: "text/plain;charset=utf-8" });
    downloadBlob(blob, `${sanitizeFilename(episodeTitle, "script")}.txt`);
  };

  return (
    <GlassModal
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-[640px] max-w-[96vw]"
      panelClassName="flex max-h-[80vh] flex-col"
    >
      {/* Header */}
      <div
        className="flex items-center gap-3 px-5 py-4"
        style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
      >
        <span
          aria-hidden
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
          style={{
            background: "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
            boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
          }}
        >
          <FileText className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3
            id={titleId}
            className="display-serif truncate text-[15px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("reference_overview_title")}
          </h3>
          <div
            className="num text-[10px] uppercase"
            style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
          >
            {t("reference_overview_summary", { count: units.length, seconds: totalSeconds })}
          </div>
        </div>
        <ModalCloseButton onClick={onClose} ariaLabel={t("reference_overview_close")} />
      </div>

      {/* Body */}
      <div className="flex-1 space-y-3 overflow-y-auto overscroll-contain px-5 py-4">
        {units.map((u) => (
          <article
            key={u.unit_id}
            className="rounded-[10px] p-3.5"
            style={{
              background: "oklch(0.20 0.011 265 / 0.4)",
              border: "1px solid var(--color-hairline)",
            }}
          >
            <div className="mb-2 flex items-center gap-2">
              <span
                translate="no"
                className="rounded px-2 py-0.5 font-mono text-[11px] font-bold tracking-wider text-[oklch(0.14_0_0)] [background:linear-gradient(180deg,var(--color-accent-2),var(--color-accent))]"
              >
                {u.unit_id}
              </span>
              <span className="inline-flex items-center gap-1 rounded border border-[var(--color-hairline-soft)] bg-[oklch(0.22_0.011_265_/_0.6)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-2)]">
                <Clock className="h-3 w-3" aria-hidden="true" />
                {t("duration_seconds_value_text", { value: u.duration_seconds })}
              </span>
            </div>
            <ScriptHighlight text={u.text} lookup={lookup} className="text-[13px]" />
          </article>
        ))}
      </div>

      {/* Footer */}
      <div
        className="flex items-center gap-2 px-5 py-3"
        style={{
          borderTop: "1px solid var(--color-hairline-soft)",
          background: "oklch(0.17 0.010 250 / 0.5)",
        }}
      >
        <span className="flex-1" />
        <SecondaryButton
          size="sm"
          leadingIcon={<Copy className="h-3.5 w-3.5" aria-hidden="true" />}
          onClick={() => void handleCopyAll()}
        >
          {t("reference_overview_copy_all")}
        </SecondaryButton>
        <SecondaryButton
          size="sm"
          leadingIcon={<Download className="h-3.5 w-3.5" aria-hidden="true" />}
          onClick={handleDownload}
        >
          {t("reference_overview_download")}
        </SecondaryButton>
      </div>
    </GlassModal>
  );
}
