import type { ReactNode } from "react";

type Variant = "display" | "picker";

interface Props {
  imageUrl: string | null | undefined;
  alt: string;
  fallback: ReactNode;
  variant: Variant;
}

export function AssetThumb({ imageUrl, alt, fallback, variant }: Props) {
  const containerClass =
    variant === "display"
      ? "aspect-video bg-gradient-to-br from-gray-800 to-gray-700 flex items-center justify-center"
      : "aspect-video bg-gray-700 rounded flex items-center justify-center text-gray-500 text-xs";
  const imgClass =
    variant === "display"
      ? "h-full w-full object-cover"
      : "h-full w-full object-cover rounded";
  return (
    <div className={containerClass}>
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
