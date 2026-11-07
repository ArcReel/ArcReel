import type { ReactNode } from "react";
import { cn } from "cn";

/** Asset thumbnail: 16:9 backing frame, showing the entire image without cropping; show a placeholder when absent. */
export function AssetThumb({
  imageUrl,
  alt,
  fallback,
  className,
}: {
  imageUrl: string | null | undefined;
  /** Pass an empty string when the asset name is already adjacent, avoiding duplicate screen-reader announcements. */
  alt: string;
  fallback: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex aspect-video items-center justify-center overflow-hidden bg-muted text-muted-foreground", className)}>
      {imageUrl ? (
        <img src={imageUrl} alt={alt} loading="lazy" decoding="async" className="size-full object-contain" />
      ) : (
        fallback
      )}
    </div>
  );
}
