import { useState } from "react";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function AssetImageDialog({
  src,
  title,
  alt,
  onClose,
}: {
  src: string | null;
  title: string;
  alt: string;
  onClose: () => void;
}) {
  const [shown, setShown] = useState(src);
  if (src !== null && src !== shown) setShown(src);
  return (
    <Dialog
      open={src !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
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
