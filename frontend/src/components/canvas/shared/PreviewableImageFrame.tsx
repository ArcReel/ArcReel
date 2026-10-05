import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ZoomIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface PreviewableImageFrameProps {
  src: string | null;
  alt: string;
  children: ReactNode;
}

/**
 * 给缩略图加「查看大图」：右上角的放大按钮在悬停或聚焦时出现（触屏上常显），点开后用占满视口的
 * 查看器显示原图。
 */
export function PreviewableImageFrame({ src, alt, children }: PreviewableImageFrameProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const label = t("image_fullscreen_preview", { name: alt });

  return (
    <>
      <div className="group relative">
        {children}
        {src && (
          <div className="absolute top-1.5 right-1.5 opacity-0 transition-opacity duration-fast group-focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:opacity-100">
            <Button
              variant="secondary"
              size="icon-sm"
              onClick={(event) => {
                event.stopPropagation();
                setOpen(true);
              }}
              aria-label={label}
            >
              <ZoomIn aria-hidden />
            </Button>
          </div>
        )}
      </div>

      {src && (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent size="viewer">
            <DialogHeader>
              <DialogTitle>{alt}</DialogTitle>
            </DialogHeader>
            <DialogBody>
              <img src={src} alt={alt} className="size-full object-contain" />
            </DialogBody>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
