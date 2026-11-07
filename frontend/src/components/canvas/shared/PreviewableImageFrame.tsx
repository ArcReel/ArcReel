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
 * Add full-size viewing to thumbnails: the top-right enlargement button appears on hover or focus (always visible on touchscreens), opening
 * a viewport-filling viewer of the original image.
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
          {/* Keep the viewing area dark: light backgrounds interfere with judgments of image brightness */}
          <DialogContent size="viewer" className="dark">
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
