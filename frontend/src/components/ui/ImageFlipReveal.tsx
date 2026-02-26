import { type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";

// ---------------------------------------------------------------------------
// ImageFlipReveal — 图片切换时的 3D 翻转动画
// ---------------------------------------------------------------------------

interface ImageFlipRevealProps {
  src: string | null;
  alt: string;
  className?: string;
  fallback?: ReactNode;
  onError?: () => void;
}

export function ImageFlipReveal({
  src,
  alt,
  className,
  fallback,
  onError,
}: ImageFlipRevealProps) {
  return (
    <div style={{ perspective: 800 }} className="h-full w-full">
      <AnimatePresence mode="wait">
        {src ? (
          <motion.img
            key={src}
            src={src}
            alt={alt}
            className={className ?? "h-full w-full object-cover"}
            initial={{ rotateY: 90, opacity: 0 }}
            animate={{ rotateY: 0, opacity: 1 }}
            exit={{ rotateY: -90, opacity: 0 }}
            transition={{ duration: 0.5, ease: "easeInOut" }}
            style={{ backfaceVisibility: "hidden" }}
            onError={onError}
          />
        ) : (
          <motion.div
            key="fallback"
            className="h-full w-full"
            initial={{ rotateY: 90, opacity: 0 }}
            animate={{ rotateY: 0, opacity: 1 }}
            exit={{ rotateY: -90, opacity: 0 }}
            transition={{ duration: 0.5, ease: "easeInOut" }}
            style={{ backfaceVisibility: "hidden" }}
          >
            {fallback}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
