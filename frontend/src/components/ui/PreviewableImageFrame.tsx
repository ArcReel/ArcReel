import { useState, type ReactNode } from "react";
import { ZoomIn } from "lucide-react";
import { ImageLightbox } from "./ImageLightbox";

interface PreviewableImageFrameProps {
  src: string | null;
  alt: string;
  children: ReactNode;
  buttonClassName?: string;
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
            aria-label={`${alt} fullscreen preview`}
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
