import { useId, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Wand2 } from "lucide-react";
import { enqueueImageEdit } from "@/actions/generation";
import { GlassModal } from "@/components/ui/GlassModal";
import { useAppStore } from "@/stores/app-store";
import { selectActiveResourceIds, selectHasActiveTaskForScriptFile, useTasksStore } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";

export type ImageEditResourceType =
  | "character"
  | "scene"
  | "prop"
  | "product"
  | "storyboard";

interface ImageEditButtonProps {
  projectName: string;
  resourceType: ImageEditResourceType;
  resourceId: string;
  /** 分镜编辑必带的剧集文件；其余资产类型忽略 */
  scriptFile?: string | null;
  /** 是否存在可编辑的当前图；无图时禁用并提示先生成/上传 */
  hasImage: boolean;
  /** 资源被生成/编辑任务占用：禁用编辑入口 */
  busy?: boolean;
}

const FIELD_STYLE: CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

/**
 * 图片卡片头部的图标式编辑入口：7x7 图标按钮 + 指令输入弹窗。
 * 提交即以当前图为底图、指令为 prompt 入队 i2i 编辑；完成后经 SSE fingerprint 自动刷新。
 */
export function ImageEditButton({
  projectName,
  resourceType,
  resourceId,
  scriptFile,
  hasImage,
  busy = false,
}: ImageEditButtonProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const titleId = useId();
  const descId = useId();
  const fieldId = useId();

  const disabled = busy || !hasImage;
  const triggerTitle = hasImage
    ? t("image_edit_action")
    : t("image_edit_no_image_hint");

  const close = () => {
    if (submitting) return;
    setOpen(false);
  };

  const handleSubmit = async () => {
    const trimmed = instruction.trim();
    // disabled（busy||!hasImage）仍要拦：busy 还带着 store 按资源占用之外的维度——宫格模式下
    // 本集有 grid 任务在跑（切割阶段覆写多张 storyboard，见 selectHasActiveTaskForScriptFile）、
    // 卡片内正在上传底图，二者都不会出现在本资源的占用集里；且键盘快捷键提交绕过按钮的
    // disabled 属性，此处是这层判定唯一的落点。
    if (!trimmed || submitting || disabled) return;
    // 再用 getState() 新鲜读复核：弹窗停留期间响应式 busy prop 的更新依赖父组件
    // 重渲染，存在感知延迟；这里直接读 store 当前值，与 resourceType/resourceId
    // 命中同一占用槽（taskResourceKind 对 image_edit 按 resource_type 归槽）。
    const { tasks, optimisticActive, optimisticActiveScriptFile } = useTasksStore.getState();
    if (selectActiveResourceIds(tasks, resourceType, projectName, optimisticActive).has(resourceId)) {
      useAppStore.getState().pushToast(t("image_edit_resource_busy"), "error");
      return;
    }
    // storyboard 资源占用集查不到 grid 任务（其 resource_id 是 grid_id）；宫格模式下
    // 本集有切割任务在跑时需按 scriptFile 复核，否则新鲜读会漏过 busy prop 尚未追上
    // 的这一维度，见 selectHasActiveTaskForScriptFile 与 GridImageToVideoCanvas 的
    // gridActiveForEpisode。
    if (
      resourceType === "storyboard" &&
      scriptFile &&
      selectHasActiveTaskForScriptFile(tasks, "grid", scriptFile, projectName, optimisticActiveScriptFile)
    ) {
      useAppStore.getState().pushToast(t("image_edit_resource_busy"), "error");
      return;
    }
    setSubmitting(true);
    try {
      await enqueueImageEdit(projectName, {
        resourceType,
        resourceId,
        instruction: trimmed,
        scriptFile: resourceType === "storyboard" ? scriptFile ?? null : null,
      });
      setInstruction("");
      setOpen(false);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={disabled}
        title={triggerTitle}
        aria-label={t("image_edit_action")}
        className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
        style={{ color: "var(--color-text-3)" }}
      >
        <Wand2 className="h-3.5 w-3.5" aria-hidden="true" />
      </button>

      <GlassModal
        open={open}
        onClose={close}
        labelledBy={titleId}
        describedBy={descId}
        closeOnBackdrop={!submitting}
        closeOnEscape={!submitting}
      >
        <div className="p-5">
          <h2
            id={titleId}
            className="display-serif text-[17px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("image_edit_modal_title")}
          </h2>
          <p
            id={descId}
            className="mt-1.5 text-[12.5px] leading-[1.55]"
            style={{ color: "var(--color-text-3)" }}
          >
            {t("image_edit_modal_desc", { name: resourceId })}
          </p>

          <label
            htmlFor={fieldId}
            className="mt-4 block text-[10px] font-semibold uppercase tracking-[0.12em]"
            style={{ color: "var(--color-text-4)" }}
          >
            {t("image_edit_instruction_label")}
          </label>
          <textarea
            id={fieldId}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                void handleSubmit();
              }
            }}
            rows={3}
            // 弹窗打开即聚焦指令输入，符合"点开就写"的心智
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
            placeholder={t("image_edit_instruction_placeholder")}
            className="focus-ring mt-1.5 w-full resize-none rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
            style={FIELD_STYLE}
          />

          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={close}
              disabled={submitting}
              className="focus-ring rounded-md px-3 py-1.5 text-[12px] font-medium transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-50"
              style={{ color: "var(--color-text-2)" }}
            >
              {t("common:cancel")}
            </button>
            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={submitting || instruction.trim().length === 0 || disabled}
              className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                color: "oklch(0.14 0 0)",
                background:
                  "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                boxShadow:
                  "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
              }}
            >
              <Wand2 className="h-3.5 w-3.5" aria-hidden="true" />
              {submitting ? t("image_edit_submitting") : t("image_edit_submit")}
            </button>
          </div>
        </div>
      </GlassModal>
    </>
  );
}
