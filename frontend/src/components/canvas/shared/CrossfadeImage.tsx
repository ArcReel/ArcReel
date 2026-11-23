import { useState, type ReactNode } from "react";
import { cn } from "cn";

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
  className?: string;
  loading?: "lazy" | "eager";
  onError?: () => void;
}) {
  const [shown, setShown] = useState<{ src: string | null; loaded: boolean; failed: boolean; previous: string | null }>({
    src,
    loaded: false,
    failed: false,
    previous: null,
  });

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
