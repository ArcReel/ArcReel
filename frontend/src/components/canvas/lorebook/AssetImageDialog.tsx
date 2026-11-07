import { useState } from "react";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/**
 * View a full-size image inside the detail Sheet. Render within its subtree: Sheet is modal and would treat an overlay mounted elsewhere
 * as an outside click, closing the Sheet.
 */
export function AssetImageDialog({
  src,
  title,
  alt,
  onClose,
}: {
  /** null closes the dialog. */
  src: string | null;
  title: string;
  alt: string;
  onClose: () => void;
}) {
  // Retain the previous image during closing animation
  const [shown, setShown] = useState(src);
  if (src !== null && src !== shown) setShown(src);
  return (
    <Dialog
      open={src !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {/* Keep the viewing area dark: light backgrounds interfere with judgments of image brightness */}
      <DialogContent size="xl" className="dark">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          {shown && <img src={shown} alt={alt} className="mx-auto max-h-[70dvh] w-auto max-w-full object-contain" />}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
