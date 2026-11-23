import type { ReactNode } from "react";
import { cn } from "cn";

export function AssetThumb({
  imageUrl,
  alt,
  fallback,
  className,
}: {
  imageUrl: string | null | undefined;
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
