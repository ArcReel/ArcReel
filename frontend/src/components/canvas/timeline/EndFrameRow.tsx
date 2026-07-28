import { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { InlineWarning } from "@/components/ui/InlineWarning";
import { useModelCapabilities } from "@/hooks/useModelCapabilities";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import type { EditorContentMode } from "@/utils/script-shape";
import { errMsg } from "@/utils/async";
import { EndFramePicker } from "./EndFramePicker";

interface EndFrameRowProps {
  projectName: string;
  segmentId: string;
  scriptFile: string;
  contentMode: EditorContentMode;
  aspectRatio: "9:16" | "16:9";
  /** 当前已设置的尾帧快照路径（项目内相对路径），未设置为 null。 */
  endFramePath: string | null;
  /** 项目级视频后端，变更后重新解析尾帧能力。 */
  videoBackend?: string | null;
  /** 只读上下文（如剧本不可编辑时）：仅展示，不给写入入口。 */
  readOnly?: boolean;
  /** 提交在途状态回传：供父级同步禁用同卡片的视频生成 / 上传 / 恢复控件。 */
  onSubmittingChange?: (submitting: boolean) => void;
  /** 视频卡的手动上传占用：同镜头视频文件正在上传时反向禁用本行的写入通道，避免与其共享的资产落盘并发冲突。 */
  videoUploadBusy?: boolean;
}

/**
 * 镜头尾帧设置行：收起显示摘要（未设置 / 已设置），展开为预览 + 说明 + 更换 / 清除。
 *
 * 占用态按仓库规范做三项检查：本镜头视频任务占用时两个写入控件同步禁用（开窗校验 +
 * 兄弟控件同步），提交时刻再从 store 直读一次最新占用态（打开面板后状态可能已变化）。
 * 源图侧零占用——尾帧是快照复制，与源图的生成任务无关；分镜 / 宫格任务同样不参与判定。
 *
 * 能力不支持不阻断控件，改由常驻的行内警告承载：模型不支持尾帧且本镜头已设过尾帧时，
 * 折叠头下方显示告警 + 「清除」一键修正入口，收起状态也可见——这条尾帧会让视频生成在
 * 执行期被后端拒绝，藏在折叠面板里等于让用户逐个失败后才知道。能力值经 useModelCapabilities
 * 取 `lastFrame` 生效值（已含用户覆盖），前端不自建判定；查询失败时按「未知」处理，
 * 不谎报不支持。改模型 / 改能力覆盖由该管线自动失效重取，警告的增减不依赖展开面板。
 */
export function EndFrameRow({
  projectName,
  segmentId,
  scriptFile,
  contentMode,
  aspectRatio,
  endFramePath,
  videoBackend,
  readOnly = false,
  onSubmittingChange,
  videoUploadBusy = false,
}: EndFrameRowProps) {
  const { t } = useTranslation("dashboard");
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const viewOnly = useDemoWorkbench() || readOnly;

  const { lastFrame, loading: capsLoading } = useModelCapabilities({ projectName, videoBackend });
  // 未查到能力（加载中 / 失败）时不谎报不支持：仅明确的 false 才门控。
  const unsupported = lastFrame === false;

  const videoBusyIds = useActiveResourceIds("video", projectName);
  // 占用不区分来源（任务队列在跑 / 视频卡手动上传在途）：二者都在写同一份 project.json，
  // 并发写入都要拦。能力不支持不在此列——它不禁用任何控件，只出警告。
  const videoBusy = videoBusyIds.has(segmentId) || videoUploadBusy;
  const fp = useProjectsStore((s) => (endFramePath ? s.getAssetFingerprint(endFramePath) : null));

  // 兄弟控件同步：更换 / 清除 / 选图器的提交入口共读这一个值。
  // 只含占用维度——能力维度（不支持、以及「尚未查到」）一律不参与门控：既然不支持都不
  // 拦，等待查询结果更没有可拦的理由，否则换模型后又会凭能力管线短暂灰掉写入控件。
  const controlsDisabled = videoBusy || submitting || viewOnly;

  // 灰化控件的 hover 原因。
  const disabledHint = !viewOnly && videoBusy ? t("end_frame_busy_hint") : undefined;

  // 已设尾帧 + 模型明确不支持才告警：未设尾帧的镜头没有会被拒绝的东西，不该打扰。
  const showUnsupportedNotice = unsupported && !!endFramePath;

  /**
   * 提交时刻复核最新占用态：面板 / 选图器打开后本镜头可能已被入队，只查开窗时刻会留
   * 一个竞态窗口。命中则拒绝并给出可见反馈。
   */
  const rejectIfDisabled = (): boolean => {
    if (videoUploadBusy) {
      useAppStore.getState().pushToast(t("end_frame_busy_hint"), "info");
      return true;
    }
    if (!isResourceBusy("video", projectName, segmentId)) return false;
    useAppStore.getState().pushToast(t("end_frame_busy_hint"), "info");
    return true;
  };

  const updateSubmitting = (value: boolean) => {
    setSubmitting(value);
    onSubmittingChange?.(value);
  };

  const runWrite = async (action: () => Promise<unknown>, successKey: string) => {
    if (rejectIfDisabled()) return;
    updateSubmitting(true);
    try {
      await action();
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("end_frame_action_failed", { message: errMsg(err) }), "error");
      return;
    } finally {
      updateSubmitting(false);
    }
    setPickerOpen(false);
    useAppStore.getState().pushToast(t(successKey, { id: segmentId }), "success");
    // 快照路径固定、换图原地覆盖，须重取项目数据拿新的资产指纹才能 cache-bust。
    // refreshProject 内部吞掉请求错误、以返回值表达结果（从不 reject）：写入已经成功，
    // 刷新失败要单独提示，不能把它误报成尾帧写入失败；刷新被项目切换取消则不代表出错，
    // 静默跳过，否则设置尾帧后立刻切项目会误报一条刷新失败。
    const refreshResult = await useProjectsStore.getState().refreshProject(projectName);
    if (refreshResult === "failed") {
      useAppStore.getState().pushToast(t("end_frame_refresh_failed"), "error");
    }
  };

  const handlePickProjectImage = (sourcePath: string) =>
    void runWrite(
      () => API.selectEndFrame(projectName, segmentId, scriptFile, sourcePath),
      "end_frame_set_success",
    );

  const handlePickUpload = (file: File) =>
    void runWrite(
      () => API.uploadEndFrame(projectName, segmentId, scriptFile, file),
      "end_frame_set_success",
    );

  const handleClear = () =>
    void runWrite(
      () => API.clearEndFrame(projectName, segmentId, scriptFile),
      "end_frame_clear_success",
    );

  const previewUrl = endFramePath ? API.getFileUrl(projectName, endFramePath, fp) : null;

  // 摘要只说尾帧本身设没设：能力不支持由下方警告条承载，混进摘要会盖掉「已设置」，
  // 让用户看不出自己设过一张待清除的尾帧。
  const summary = capsLoading
    ? t("end_frame_capability_checking")
    : endFramePath
      ? t("end_frame_summary_set")
      : t("end_frame_summary_unset");

  return (
    <div
      className="mb-2.5 rounded-[10px]"
      style={{
        border: "1px solid var(--color-hairline)",
        background: "oklch(0.18 0.010 265 / 0.4)",
      }}
    >
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        aria-controls={panelId}
        className="focus-ring flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          aria-hidden
          className="h-3.5 w-3.5 transition-transform"
          style={{
            color: "var(--color-text-3)",
            transform: expanded ? "rotate(90deg)" : undefined,
          }}
        />
        <span className="text-[12px] font-semibold" style={{ color: "var(--color-text-2)" }}>
          {t("end_frame_title")}
        </span>
        <span className="flex-1" />
        {previewUrl && (
          <img
            src={previewUrl}
            alt=""
            aria-hidden
            className="h-4 w-2.5 rounded-[3px] object-cover"
            style={{ border: "1px solid var(--color-accent-soft)" }}
          />
        )}
        <span
          className="text-[11px]"
          // 摘要随能力查询异步变化（检查中 → 已设置 / 未设置），朗读器需要跟上
          aria-live="polite"
          style={{
            color: endFramePath ? "var(--color-accent-2)" : "var(--color-text-4)",
          }}
        >
          {summary}
        </span>
      </button>

      {/* 常驻警告：收起状态也可见，让「已设尾帧后切到不支持的模型」在入队前就暴露。 */}
      {showUnsupportedNotice && (
        <InlineWarning
          className="px-3 pb-2"
          message={t("end_frame_unsupported_notice")}
          action={
            viewOnly
              ? undefined
              : {
                  // 展开时面板内另有一个「清除」，两个按钮同屏：这里用全称，
                  // 否则屏幕阅读器读到两个同名按钮无从区分。
                  label: t("end_frame_clear_end_frame"),
                  onClick: handleClear,
                  disabled: controlsDisabled,
                  title: disabledHint,
                }
          }
        />
      )}

      {expanded && (
        <div
          id={panelId}
          className="flex items-start gap-3 px-3 pb-3 pt-1"
          style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
        >
          <div
            className="w-16 shrink-0 overflow-hidden rounded-[6px]"
            style={{
              border: previewUrl
                ? "1px solid var(--color-accent-soft)"
                : "1px dashed var(--color-hairline-strong)",
              background: previewUrl ? undefined : "oklch(0.20 0.011 265 / 0.5)",
            }}
          >
            <AspectFrame ratio={aspectRatio}>
              {previewUrl ? (
                <img
                  src={previewUrl}
                  alt={t("end_frame_preview_alt", { id: segmentId })}
                  className="h-full w-full object-cover"
                />
              ) : (
                <div
                  className="grid h-full w-full place-items-center text-[9.5px]"
                  style={{ color: "var(--color-text-4)" }}
                >
                  {t("end_frame_summary_unset")}
                </div>
              )}
            </AspectFrame>
          </div>
          <div className="flex flex-1 flex-col gap-2 pt-1">
            <p className="text-[11px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>
              {t("end_frame_description")}
            </p>
            {/* 展开面板讲恢复路径（改模型 / 调能力覆盖），与警告条的「后果 + 清除」互补；
                未设尾帧时也给，让用户在动手设之前就知道这个模型设了也白设。 */}
            {unsupported && (
              <p className="text-[11px] leading-relaxed" style={{ color: "var(--color-text-4)" }}>
                {t("end_frame_unsupported_hint")}
              </p>
            )}
            {!viewOnly && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    if (rejectIfDisabled()) return;
                    setPickerOpen(true);
                  }}
                  disabled={controlsDisabled}
                  title={disabledHint}
                  className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)] disabled:cursor-not-allowed disabled:opacity-50"
                  style={{
                    border: "1px solid var(--color-hairline)",
                    background: "oklch(0.22 0.011 265 / 0.5)",
                    color: "var(--color-text-2)",
                  }}
                >
                  {endFramePath ? t("end_frame_replace") : t("end_frame_choose")}
                </button>
                {endFramePath && (
                  <button
                    type="button"
                    onClick={handleClear}
                    disabled={controlsDisabled}
                    title={disabledHint}
                    className="focus-ring rounded-md px-2.5 py-1 text-[11.5px] transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)] disabled:cursor-not-allowed disabled:opacity-50"
                    style={{ color: "var(--color-text-3)" }}
                  >
                    {t("end_frame_clear")}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {pickerOpen && (
        <EndFramePicker
          projectName={projectName}
          scriptFile={scriptFile}
          contentMode={contentMode}
          aspectRatio={aspectRatio}
          submitting={submitting}
          disabled={videoBusy}
          onClose={() => setPickerOpen(false)}
          onPickProjectImage={handlePickProjectImage}
          onPickUpload={handlePickUpload}
        />
      )}
    </div>
  );
}
