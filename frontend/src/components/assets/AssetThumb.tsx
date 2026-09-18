import type { ReactNode } from "react";

type Variant = "display" | "picker";

interface Props {
  imageUrl: string | null | undefined;
  alt: string;
  fallback: ReactNode;
  variant: Variant;
}

const DISPLAY_BG = "var(--color-surface-muted)";
const PICKER_BG = "var(--color-surface-muted)";

export function AssetThumb({ imageUrl, alt, fallback, variant }: Props) {
  const isDisplay = variant === "display";
  const containerClass = isDisplay
    ? "aspect-video flex items-center justify-center text-text-4"
    : "aspect-video flex items-center justify-center rounded text-text-4 text-xs";
  const imgClass = isDisplay
    ? "h-full w-full object-contain"
    : "h-full w-full object-contain rounded";
  return (
    <div className={containerClass} style={{ background: isDisplay ? DISPLAY_BG : PICKER_BG }}>
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={alt}
          loading="lazy"
          decoding="async"
          className={imgClass}
        />
      ) : (
        fallback
      )}
    </div>
  );
}
