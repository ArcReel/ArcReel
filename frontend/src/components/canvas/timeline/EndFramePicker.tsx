import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ImagePlus, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { UPLOAD_IMAGE_ACCEPT } from "@/components/ui/UploadIconButton";
import { useProjectsStore } from "@/stores/projects-store";
import { getScriptItems, getScriptItemId, type EditorContentMode } from "@/utils/script-shape";
import { stripScriptsPrefix } from "@/utils/task-target";

/** 可选的项目内图片：path 是项目内相对路径，交给 /end-frame/select 做快照复制。 */
interface PickableImage {
  path: string;
  label: string;
}

interface ImageGroup {
  /** 分组标题（已翻译） */
  title: string;
  images: PickableImage[];
}

interface EndFramePickerProps {
  projectName: string;
  scriptFile: string;
  contentMode: EditorContentMode;
  aspectRatio: "9:16" | "16:9";
  onClose: () => void;
  /** 选定项目内图片：交由父级调用设置 API（父级负责提交时刻的占用复核）。 */
  onPickProjectImage: (sourcePath: string) => void;
  /** 选定本地文件上传：同上。 */
  onPickUpload: (file: File) => void;
  /** 设置请求在途：禁用确认，避免重复提交。 */
  submitting?: boolean;
  /** 弹窗打开后禁用态发生变化（如能力被判定为不支持、本镜头被入队）：与提交在途一并禁用写入通道。 */
  disabled?: boolean;
}

/**
 * 尾帧选图器：单选，两条通道同一落点。
 *
 * 通道一是项目内图片按来源分组平铺（本集分镜图 / 本集宫格切图），
 * 选定后走 `/end-frame/select` 按相对路径快照复制；通道二是 header 的上传入口，
 * 走 `/end-frame/upload`。两者都不建立对源图的引用，源图后续变动不影响尾帧。
 */
export function EndFramePicker({
  projectName,
  scriptFile,
  contentMode,
  aspectRatio,
  onClose,
  onPickProjectImage,
  onPickUpload,
  submitting = false,
  disabled = false,
}: EndFramePickerProps) {
  const writeDisabled = submitting || disabled;
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const [selected, setSelected] = useState<PickableImage | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const script = useProjectsStore((s) => s.currentScripts[scriptFile]);
  const [gridImages, setGridImages] = useState<PickableImage[]>([]);

  // 宫格切图不在 store 里，弹窗打开时拉一次。取消走 AbortSignal：
  // 弹窗在响应到达前被关闭时，不再往已卸载组件写 state。
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const normalized = stripScriptsPrefix(scriptFile);
    API.listGrids(projectName, { signal })
      .then((grids) => {
        if (signal.aborted) return;
        setGridImages(
          grids
            .filter((g) => stripScriptsPrefix(g.script_file) === normalized)
            .flatMap((g) =>
              g.frame_chain
                .filter((cell) => cell.image_path)
                .map((cell) => ({
                  path: cell.image_path as string,
                  label: t("end_frame_picker_grid_cell_label", {
                    grid: g.grid_size,
                    index: cell.index + 1,
                  }),
                })),
            ),
        );
      })
      .catch(() => {
        if (signal.aborted) return;
        // 宫格取不到不阻断选图：其余分组仍可用，空分组自然不渲染。
        setGridImages([]);
      });
    return () => {
      controller.abort();
    };
  }, [projectName, scriptFile, t]);

  const groups = useMemo<ImageGroup[]>(() => {
    const storyboards: PickableImage[] = getScriptItems(script, contentMode)
      .filter((item) => item.generated_assets?.storyboard_image)
      .map((item) => {
        const id = getScriptItemId(item, contentMode);
        return {
          path: item.generated_assets!.storyboard_image as string,
          label: t("end_frame_picker_storyboard_label", { id }),
        };
      });

    return [
      { title: t("end_frame_picker_group_storyboards"), images: storyboards },
      { title: t("end_frame_picker_group_grid_cells"), images: gridImages },
    ].filter((g) => g.images.length > 0);
  }, [script, contentMode, gridImages, t]);

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-[720px] max-w-[96vw]"
      panelClassName="flex max-h-[85vh] flex-col"
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
            background:
              "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
            boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
          }}
        >
          <ImagePlus className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3
            id={titleId}
            className="display-serif truncate text-[15px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("end_frame_picker_title")}
          </h3>
          <div
            className="num text-[10px] uppercase"
            style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
          >
            {t("end_frame_picker_eyebrow")}
          </div>
        </div>

        <input
          ref={fileRef}
          type="file"
          accept={UPLOAD_IMAGE_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) onPickUpload(f);
          }}
        />
        <SecondaryButton
          size="sm"
          disabled={writeDisabled}
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="h-3.5 w-3.5" aria-hidden />
          <span>{t("end_frame_picker_upload")}</span>
        </SecondaryButton>
        <ModalCloseButton onClick={onClose} ariaLabel={t("end_frame_picker_close")} />
      </div>

      {/* Grouped grid */}
      <div className="flex-1 overflow-y-auto overscroll-contain p-4">
        {groups.length === 0 ? (
          <p className="px-1 py-6 text-center text-[12px]" style={{ color: "var(--color-text-4)" }}>
            {t("end_frame_picker_empty")}
          </p>
        ) : (
          groups.map((grp) => (
            <div key={grp.title} className="mb-4 last:mb-0">
              <h4
                className="num mb-2 text-[10px] font-bold uppercase"
                style={{ color: "var(--color-text-3)", letterSpacing: "1.2px" }}
              >
                {grp.title}
                <span className="ml-1.5" style={{ color: "var(--color-text-4)" }}>
                  {grp.images.length}
                </span>
              </h4>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                {grp.images.map((img) => (
                  <PickerCell
                    key={img.path}
                    projectName={projectName}
                    image={img}
                    aspectRatio={aspectRatio}
                    selected={selected?.path === img.path}
                    onToggle={() =>
                      setSelected((prev) => (prev?.path === img.path ? null : img))
                    }
                  />
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div
        className="flex items-center gap-2 px-5 py-3"
        style={{
          borderTop: "1px solid var(--color-hairline-soft)",
          background: "oklch(0.17 0.010 250 / 0.5)",
        }}
      >
        <span className="flex-1 truncate text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {selected
            ? t("end_frame_picker_selected", { label: selected.label })
            : t("end_frame_picker_hint")}
        </span>
        <SecondaryButton size="sm" onClick={onClose} disabled={submitting}>
          {t("end_frame_picker_cancel")}
        </SecondaryButton>
        <PrimaryButton
          size="sm"
          disabled={!selected || writeDisabled}
          onClick={() => selected && onPickProjectImage(selected.path)}
        >
          {submitting ? t("end_frame_picker_submitting") : t("end_frame_picker_confirm")}
        </PrimaryButton>
      </div>
    </GlassModal>
  );
}

interface PickerCellProps {
  projectName: string;
  image: PickableImage;
  aspectRatio: "9:16" | "16:9";
  selected: boolean;
  onToggle: () => void;
}

function PickerCell({ projectName, image, aspectRatio, selected, onToggle }: PickerCellProps) {
  const fp = useProjectsStore((s) => s.getAssetFingerprint(image.path));
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onToggle}
      title={image.label}
      className="focus-ring relative overflow-hidden rounded-lg text-left transition-transform hover:-translate-y-px"
      style={{
        border: selected
          ? "1px solid var(--color-accent-soft)"
          : "1px solid var(--color-hairline)",
        boxShadow: selected
          ? "0 6px 18px -6px var(--color-accent-glow)"
          : "inset 0 1px 0 oklch(1 0 0 / 0.03)",
      }}
    >
      <AspectFrame ratio={aspectRatio}>
        <img
          src={API.getFileUrl(projectName, image.path, fp)}
          alt={image.label}
          loading="lazy"
          className="h-full w-full object-cover"
        />
      </AspectFrame>
      <div
        className="truncate px-1.5 py-1 text-[10.5px] font-medium"
        style={{ color: "var(--color-text-2)", background: "oklch(0.16 0.010 265 / 0.85)" }}
      >
        {image.label}
      </div>
      {selected && (
        <span
          aria-hidden
          className="absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full"
          style={{
            color: "oklch(0.14 0 0)",
            background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 0 0 1px var(--color-accent-soft)",
          }}
        >
          <Check className="h-3 w-3" strokeWidth={3} />
        </span>
      )}
    </button>
  );
}
