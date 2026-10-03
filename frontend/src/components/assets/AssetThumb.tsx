import type { ReactNode } from "react";
import { cn } from "cn";

/** 资产图缩略：16:9 底框，图片完整显示不裁切；没有图时显示占位。 */
export function AssetThumb({
  imageUrl,
  alt,
  fallback,
  className,
}: {
  imageUrl: string | null | undefined;
  /** 旁边已有资产名时传空串，读屏不重复读名称。 */
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
