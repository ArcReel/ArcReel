import { motion } from "framer-motion";

// ---------------------------------------------------------------------------
// Ratio → Tailwind class mapping
// ---------------------------------------------------------------------------

const RATIO_CLASSES: Record<string, string> = {
  "9:16": "aspect-[9/16]",
  "16:9": "aspect-video",
  "3:4": "aspect-[3/4]",
  "1:1": "aspect-square",
};

// ---------------------------------------------------------------------------
// AspectFrame
// ---------------------------------------------------------------------------

interface AspectFrameProps {
  ratio: "9:16" | "16:9" | "3:4" | "1:1";
  children: React.ReactNode;
  className?: string;
}

export function AspectFrame({ ratio, children, className }: AspectFrameProps) {
  const ratioClass = RATIO_CLASSES[ratio] ?? RATIO_CLASSES["16:9"];

  return (
    <motion.div
      layout
      className={`overflow-hidden rounded-lg bg-gray-800 ${ratioClass} ${className ?? ""}`}
    >
      {children}
    </motion.div>
  );
}
