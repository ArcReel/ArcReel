import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { AspectFrame } from "@/components/canvas/shared/AspectFrame";
import { UPLOAD_IMAGE_ACCEPT } from "@/components/canvas/shared/UploadIconButton";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "cn";
import { useProjectsStore } from "@/stores/projects-store";
import { getScriptItems, getScriptItemId, type EditorContentMode } from "@/utils/script-shape";
import { stripScriptsPrefix } from "@/utils/task-target";
import { itemIdWithinEpisode } from "@/utils/episode-display";

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
  /** 弹窗打开后占用态发生变化（如本分镜被入队）：与提交在途一并禁用写入通道。 */
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
          label: t("end_frame_picker_storyboard_label", { id: itemIdWithinEpisode(id) }),
        };
      });

    return [
      { title: t("end_frame_picker_group_storyboards"), images: storyboards },
      { title: t("end_frame_picker_group_grid_cells"), images: gridImages },
    ].filter((g) => g.images.length > 0);
  }, [script, contentMode, gridImages, t]);

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        // 设置请求在途时忽略关闭：结果回来前关掉，成功与失败都无处落脚。
        if (!open && !submitting) onClose();
      }}
    >
      <DialogContent size="lg" showCloseButton={false}>
        <DialogHeader>
          <div className="flex items-center gap-3">
            <DialogTitle className="min-w-0 flex-1">{t("end_frame_picker_title")}</DialogTitle>
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
            <Button variant="outline" size="sm" disabled={writeDisabled} onClick={() => fileRef.current?.click()}>
              <Upload aria-hidden data-icon="inline-start" />
              {t("end_frame_picker_upload")}
            </Button>
          </div>
        </DialogHeader>

        <DialogBody>
          {groups.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">{t("end_frame_picker_empty")}</p>
          ) : (
            <div className="flex flex-col gap-4">
              {groups.map((grp) => (
                <section key={grp.title} className="flex flex-col gap-2">
                  <h3 className="text-sm font-medium text-foreground">
                    {grp.title}
                    <span className="num ml-1.5 font-normal text-muted-foreground">{grp.images.length}</span>
                  </h3>
                  <ul className="grid grid-cols-[repeat(auto-fill,minmax(7rem,1fr))] gap-2">
                    {grp.images.map((img) => (
                      <li key={img.path} className="flex min-w-0">
                        <PickerCell
                          projectName={projectName}
                          image={img}
                          aspectRatio={aspectRatio}
                          selected={selected?.path === img.path}
                          onToggle={() => setSelected((prev) => (prev?.path === img.path ? null : img))}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <span className="mr-auto min-w-0 truncate text-sm text-muted-foreground">
            {selected ? t("end_frame_picker_selected", { label: selected.label }) : t("end_frame_picker_hint")}
          </span>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            {t("end_frame_picker_cancel")}
          </Button>
          <Button disabled={!selected || writeDisabled} onClick={() => selected && onPickProjectImage(selected.path)}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {submitting ? t("end_frame_picker_submitting") : t("end_frame_picker_confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
      className={cn(
        "focus-ring relative flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border bg-card text-left transition-colors duration-fast",
        selected ? "border-primary" : "border-border hover:border-input",
      )}
    >
      <AspectFrame ratio={aspectRatio}>
        <img
          src={API.getFileUrl(projectName, image.path, fp)}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover"
        />
      </AspectFrame>
      <TruncatedText
        text={image.label}
        focusable={false}
        className="px-1.5 py-1 text-xs font-medium text-subtle-foreground"
      />
      {selected && (
        <span
          aria-hidden
          className="absolute top-1.5 right-1.5 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground"
        >
          <Check className="size-3" strokeWidth={3} />
        </span>
      )}
    </button>
  );
}
