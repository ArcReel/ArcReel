import { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { useVideoCapabilities } from "@/hooks/useVideoCapabilities";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import {
  selectActiveResourceIds,
  useActiveResourceIds,
  useTasksStore,
} from "@/stores/tasks-store";
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
 * 镜头尾帧设置行：收起显示三态摘要（未设置 / 已设置 / 模型不支持），展开为
 * 预览 + 说明 + 更换 / 清除。
 *
 * 占用态按仓库规范做三项检查：本镜头视频任务占用时两个写入控件同步禁用（开窗校验 +
 * 兄弟控件同步），提交时刻再从 store 直读一次最新占用态（打开面板后状态可能已变化）。
 * 源图侧零占用——尾帧是快照复制，与源图的生成任务无关；分镜 / 宫格任务同样不参与判定。
 *
 * 能力门控读 `/video-capabilities` 的 `last_frame` 生效值（已含用户覆盖）。能力查询
 * 失败时按「未知」放行控件而非禁用：后端在不支持尾帧时会拒绝生成并给出可读原因，
 * 网络抖动不该把功能锁死。
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

  const { caps, loading: capsLoading, refresh: refreshCaps } = useVideoCapabilities(
    projectName,
    videoBackend,
  );
  // 未查到能力（加载中 / 失败）时不谎报不支持：仅明确的 false 才门控。
  const unsupported = caps ? !caps.last_frame : false;

  const videoBusyIds = useActiveResourceIds("video", projectName);
  // 占用不区分来源（任务队列在跑 / 视频卡手动上传在途）：二者都在写同一份 project.json，
  // 并发写入都要拦。能力不支持是另一维度——只挡「新写入」，不挡「清除」，见下方 clearDisabled。
  const videoBusy = videoBusyIds.has(segmentId) || videoUploadBusy;
  const fp = useProjectsStore((s) => (endFramePath ? s.getAssetFingerprint(endFramePath) : null));

  // 兄弟控件同步：更换 / 选图器的提交入口共读这一个值。
  const controlsDisabled = unsupported || videoBusy || submitting || capsLoading || viewOnly;
  // 清除不受「模型不支持」门控：清掉一张已设置的尾帧不需要模型支持该能力，
  // 后端也未对 clear 做任何能力校验（纯本地资产删除），只有占用 / 在途 / 只读挡它。
  const clearDisabled = videoBusy || submitting || capsLoading || viewOnly;

  // 灰化控件的 hover 原因。不支持是模型级的稳定原因，优先于临时性的占用 / 检查中。
  const chooseDisabledHint = viewOnly
    ? undefined
    : unsupported
      ? t("end_frame_unsupported_hint")
      : videoBusy
        ? t("end_frame_busy_hint")
        : capsLoading
          ? t("end_frame_capability_checking")
          : undefined;
  const clearDisabledHint = viewOnly
    ? undefined
    : videoBusy
      ? t("end_frame_busy_hint")
      : capsLoading
        ? t("end_frame_capability_checking")
        : undefined;

  /**
   * 提交时刻复核最新禁用态：面板 / 选图器打开后能力可能已变为不支持，或本镜头
   * 可能已被入队，只查开窗时刻会留一个竞态窗口。命中则拒绝并给出可见反馈。
   * `skipUnsupportedCheck` 供清除路径使用——清除不受模型能力门控。
   */
  const rejectIfDisabled = (skipUnsupportedCheck = false): boolean => {
    if (!skipUnsupportedCheck && unsupported) {
      useAppStore.getState().pushToast(t("end_frame_unsupported_hint"), "info");
      return true;
    }
    if (videoUploadBusy) {
      useAppStore.getState().pushToast(t("end_frame_busy_hint"), "info");
      return true;
    }
    const { tasks, optimisticActive } = useTasksStore.getState();
    const active = selectActiveResourceIds(tasks, "video", projectName, optimisticActive);
    if (!active.has(segmentId)) return false;
    useAppStore.getState().pushToast(t("end_frame_busy_hint"), "info");
    return true;
  };

  const updateSubmitting = (value: boolean) => {
    setSubmitting(value);
    onSubmittingChange?.(value);
  };

  const runWrite = async (
    action: () => Promise<unknown>,
    successKey: string,
    skipUnsupportedCheck = false,
  ) => {
    if (rejectIfDisabled(skipUnsupportedCheck)) return;
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
      true,
    );

  const previewUrl = endFramePath ? API.getFileUrl(projectName, endFramePath, fp) : null;

  const summary = capsLoading
    ? t("end_frame_capability_checking")
    : unsupported
      ? t("end_frame_summary_unsupported")
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
        onClick={() => {
          const next = !expanded;
          setExpanded(next);
          // 展开是用户显式查看门控的时机：顺带重取一次能力，
          // 让「改过模型或能力覆盖」在不重挂载组件时也能反映出来。
          if (next) refreshCaps();
        }}
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
        {endFramePath && !unsupported && previewUrl && (
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
          // 摘要随能力解析异步变化（检查中 → 支持 / 不支持），朗读器需要跟上
          aria-live="polite"
          style={{
            color: endFramePath && !unsupported ? "var(--color-accent-2)" : "var(--color-text-4)",
          }}
        >
          {summary}
        </span>
      </button>

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
            {/* 不支持时的原因同时给可见文本：title 只有指针能读到，键盘用户读不到。 */}
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
                  title={chooseDisabledHint}
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
                    disabled={clearDisabled}
                    title={clearDisabledHint}
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
          disabled={unsupported || videoBusy || capsLoading}
          onClose={() => setPickerOpen(false)}
          onPickProjectImage={handlePickProjectImage}
          onPickUpload={handlePickUpload}
        />
      )}
    </div>
  );
}
