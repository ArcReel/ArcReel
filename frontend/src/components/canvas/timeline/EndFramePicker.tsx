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
import { getScriptItems, type EditorContentMode } from "@/utils/script-shape";
import { stripScriptsPrefix } from "@/utils/task-target";
import { itemPositionLabel } from "@/utils/episode-display";

/** Selectable project image: relative path goes to /end-frame/select for snapshot copying. */
interface PickableImage {
  path: string;
  label: string;
}

interface ImageGroup {
  /** Already-translated group heading. */
  title: string;
  images: PickableImage[];
}

interface EndFramePickerProps {
  projectName: string;
  scriptFile: string;
  contentMode: EditorContentMode;
  aspectRatio: "9:16" | "16:9";
  onClose: () => void;
  /** Project-image selection delegates setting APIs to parents, which own submit-time occupancy rechecks. */
  onPickProjectImage: (sourcePath: string) => void;
  /** Local upload selection, same delegation. */
  onPickUpload: (file: File) => void;
  /** Setting request in flight disables confirmation, preventing duplicates. */
  submitting?: boolean;
  /** New occupancy while open, e.g. queued shots, disables writes together with submission occupancy. */
  disabled?: boolean;
}

/**
 * End-frame picker: single selection, two channels sharing one destination.
 *
 * Project-image channel groups current episode storyboards/grid splits by origin;
 * selection snapshots relative paths through `/end-frame/select`. Header uploads use
 * `/end-frame/upload`. Neither references source images; later source changes do not affect end frames.
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

  // Grid splits are outside stores; fetch once on opening, canceled via AbortSignal:
  // closing before responses arrive prevents writes to unmounted state.
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
        // Failed grid fetches do not block other groups; empty groups naturally do not render.
        setGridImages([]);
      });
    return () => {
      controller.abort();
    };
  }, [projectName, scriptFile, t]);

  const groups = useMemo<ImageGroup[]>(() => {
    const storyboards: PickableImage[] = getScriptItems(script, contentMode)
      .flatMap((item, index) => {
        const path = item.generated_assets?.storyboard_image;
        return path ? [{ path, label: itemPositionLabel("shot", index + 1, t) }] : [];
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
        // Ignore closing during setting requests, or outcomes would have nowhere to display.
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
          src={API.getFileUrl(projectName, image.path, fp, { width: 320 })}
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
