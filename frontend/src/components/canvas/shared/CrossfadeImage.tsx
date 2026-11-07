import { useState, type ReactNode } from "react";
import { cn } from "cn";

/**
 * Crossfade image changes: keep the previous image until the new one loads, fade in above it, then remove the previous image after transition.
 * Fade in initially too; missing images or new-image failures show a placeholder rather than passing off the old image as new. Transition opacity only; leave reduced-motion handling to global rules.
 */
export function CrossfadeImage({
  src,
  alt,
  fallback,
  className,
  loading,
  onError,
}: {
  src: string | null;
  alt: string;
  fallback: ReactNode;
  /** Applied to the image element, such as `object-contain`. */
  className?: string;
  /** Pass through to the image element; list cards use `"lazy"` to load near the viewport. */
  loading?: "lazy" | "eager";
  onError?: () => void;
}) {
  const [shown, setShown] = useState<{ src: string | null; loaded: boolean; failed: boolean; previous: string | null }>({
    src,
    loaded: false,
    failed: false,
    previous: null,
  });

  // Adjust state with props: demote the loaded current image to background on changes; the new image starts transparent.
  if (shown.src !== src) {
    setShown({ src, loaded: false, failed: false, previous: shown.loaded ? shown.src : shown.previous });
  }

  if (!src || (shown.src === src && shown.failed)) return <>{fallback}</>;

  return (
    <span className="relative block size-full">
      {shown.previous && (
        <img
          src={shown.previous}
          alt=""
          aria-hidden
          loading={loading}
          decoding="async"
          className={cn("absolute inset-0 size-full", className)}
        />
      )}
      <img
        key={src}
        src={src}
        alt={alt}
        loading={loading}
        decoding="async"
        onLoad={() => setShown((current) => (current.src === src ? { ...current, loaded: true } : current))}
        onError={() => {
          setShown((current) => (current.src === src ? { ...current, failed: true, previous: null } : current));
          onError?.();
        }}
        onTransitionEnd={() => setShown((current) => (current.src === src ? { ...current, previous: null } : current))}
        className={cn(
          "relative size-full transition-opacity duration-base ease-emphasized",
          shown.loaded ? "opacity-100" : "opacity-0",
          className,
        )}
      />
    </span>
  );
}
