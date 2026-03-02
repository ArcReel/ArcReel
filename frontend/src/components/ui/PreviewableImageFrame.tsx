import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X, ZoomIn } from "lucide-react";
import { UI_LAYERS } from "@/utils/ui-layers";

interface PreviewableImageFrameProps {
  src: string | null;
  alt: string;
  children: ReactNode;
  buttonClassName?: string;
}

interface ImageLightboxProps {
  src: string;
  alt: string;
  onClose: () => void;
}

function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }

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
          onClick={onClose}
          aria-label="关闭全屏预览"
          className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/12 bg-black/55 text-white shadow-lg shadow-black/30 backdrop-blur transition-colors hover:bg-black/75"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      <div className="flex h-full w-full items-center justify-center p-5 sm:p-8 lg:p-12">
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`${alt} 全屏预览`}
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

export function PreviewableImageFrame({
  src,
  alt,
  children,
  buttonClassName,
}: PreviewableImageFrameProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className="group relative">
        {children}
        {src && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              setOpen(true);
            }}
            aria-label={`${alt} 全屏预览`}
            className={
              "absolute right-1.5 top-1.5 inline-flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-slate-950/40 text-white/84 opacity-100 shadow-[0_8px_18px_rgba(15,23,42,0.24)] backdrop-blur-md transition-all hover:-translate-y-0.5 hover:bg-slate-950/58 hover:text-white focus:outline-none focus:ring-2 focus:ring-white/24 sm:pointer-events-none sm:opacity-0 sm:group-hover:pointer-events-auto sm:group-hover:opacity-100 sm:group-focus-within:pointer-events-auto sm:group-focus-within:opacity-100 " +
              (buttonClassName ?? "")
            }
          >
            <ZoomIn className="h-3 w-3" />
          </button>
        )}
      </div>

      {open && src && (
        <ImageLightbox
          src={src}
          alt={alt}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
