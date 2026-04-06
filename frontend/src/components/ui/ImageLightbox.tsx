import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { UI_LAYERS } from "@/utils/ui-layers";

export interface ImageLightboxProps {
  src: string;
  alt: string;
  onClose: () => void;
}

export function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  if (typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div
      className={`fixed inset-0 bg-slate-950/94 backdrop-blur-sm ${UI_LAYERS.modal}`}
      onClick={onClose}
    >
      <div className="absolute right-4 top-4 sm:right-6 sm:top-6">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onClose();
          }}
          aria-label="Đóng xem trước toàn màn hình"
          className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/12 bg-black/55 text-white shadow-lg shadow-black/30 backdrop-blur transition-colors hover:bg-black/75"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="flex h-full w-full items-center justify-center p-5 sm:p-8 lg:p-12">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`${alt} xem trước toàn màn hình`}
          className="relative max-h-full max-w-full"
          onClick={(event) => event.stopPropagation()}
        >
          <img
            src={src}
            alt={alt}
            className="max-h-[calc(100vh-3rem)] max-w-[calc(100vw-2rem)] rounded-2xl border border-white/10 bg-black/35 object-contain shadow-[0_30px_120px_rgba(0,0,0,0.55)] sm:max-h-[calc(100vh-5rem)] sm:max-w-[calc(100vw-4rem)]"
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
