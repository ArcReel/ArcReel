import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { ScriptPlanConversionPreview } from "@/types";

interface ScriptPlanConversionDialogProps {
  open: boolean;
  projectName: string;
  episode: number;
  onClose: () => void;
}

/**
 * 内容确认后的「转为正式脚本」对话框：说明两条路径，推荐让 Agent 生成（内容与提示词一次完成），
 * 也允许不经模型直接把脚本规划机械转为正式脚本（提示词待生成）。
 *
 * 打开时向后端取一次只读预演：已有正式脚本时列出新增 / 失效 / 移出条目数，转换本身不改失效条目。
 */
export function ScriptPlanConversionDialog({ open, projectName, episode, onClose }: ScriptPlanConversionDialogProps) {
  const { t } = useTranslation("dashboard");
  const [preview, setPreview] = useState<ScriptPlanConversionPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [converting, setConverting] = useState(false);
  const titleId = `script-plan-conversion-${episode}`;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const next = await API.previewScriptPlanConversion(projectName, episode, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setPreview(next);
      } catch (e) {
        if (controller.signal.aborted) return;
        setPreviewError(errMsg(e));
      }
    })();
    return () => {
      controller.abort();
      // 关闭后清空：下次打开重新比对，不拿上一次的三组数说这一次。
      setPreview(null);
      setPreviewError(null);
    };
  }, [open, projectName, episode]);

  const handleAgent = () => {
    useAssistantStore.getState().setInput(t("episode_workspace_prefill_script", { episode }));
    useAppStore.getState().setAssistantPanelOpen(true);
    onClose();
  };

  const noEntryChanges =
    preview != null && preview.added.length === 0 && preview.stale.length === 0 && preview.removed.length === 0;
  // 三组条目为空不等于已同步：只调顺序或只改标题同样要转，与后端的空操作判定同口径。
  const inSync =
    preview != null && preview.has_script && noEntryChanges && !preview.order_changed && !preview.title_changed;

  const handleConvert = async () => {
    if (converting) return;
    setConverting(true);
    try {
      const receipt = await API.convertScriptPlan(projectName, episode);
      useAppStore.getState().pushToast(
        t("review_convert_done", {
          added: receipt.added.length,
          refreshed: receipt.refreshed.length,
          removed: receipt.removed.length,
        }),
        "success",
      );
      onClose();
    } catch (e) {
      useAppStore.getState().pushToast(t("review_convert_failed", { message: errMsg(e) }), "error");
      return;
    } finally {
      setConverting(false);
    }
    // 正式脚本已落盘，时间线要重取项目数据才能切到分镜视图。刷新不在转换的失败路径里：
    // refreshProject 以结算值而非 rejection 报告失败，失败只留旧数据、不把已成功的转换说成失败；
    // 但要单独提示，否则页面停在规划视图、再打开对话框又显示已同步，看着像转换没生效。
    // cancelled 是项目已切走，静默。
    const refreshed = await useProjectsStore.getState().refreshProject(projectName);
    if (refreshed === "failed") {
      useAppStore.getState().pushToast(t("review_convert_refresh_failed"), "warning");
    }
  };

  const summary = previewError
    ? t("review_convert_preview_failed", { message: previewError })
    : preview == null
      ? t("review_convert_loading")
      : !preview.has_script
        ? t("review_convert_fresh_hint", { count: preview.added.length })
        : inSync
          ? t("review_convert_in_sync")
          : noEntryChanges
            ? t("review_convert_structure_only")
            : t("review_convert_counts", {
              added: preview.added.length,
              stale: preview.stale.length,
              removed: preview.removed.length,
            });

  return (
    <GlassModal
      open={open}
      onClose={converting ? () => {} : onClose}
      labelledBy={titleId}
      hairlineTone="accent"
      closeOnBackdrop={!converting}
      closeOnEscape={!converting}
    >
      <div className="px-6 pb-6 pt-5">
        <h2
          id={titleId}
          className="display-serif text-[17px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {t("review_convert_title")}
        </h2>
        <p className="mt-1 text-[12.5px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>
          {t("review_convert_desc")}
        </p>
        <p
          role="status"
          className="mt-3 rounded-lg border border-hairline px-3 py-2 text-[12px] leading-relaxed"
          style={{ color: "var(--color-text-2)", background: "var(--color-bg-grad-a)" }}
        >
          {summary}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <SecondaryButton
            size="sm"
            onClick={() => void handleConvert()}
            disabled={converting || preview == null || inSync}
          >
            {converting ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
                {t("review_convert_converting")}
              </span>
            ) : (
              t("review_convert_direct")
            )}
          </SecondaryButton>
          <PrimaryButton size="sm" onClick={handleAgent} disabled={converting}>
            {t("review_convert_agent")}
          </PrimaryButton>
        </div>
      </div>
    </GlassModal>
  );
}
