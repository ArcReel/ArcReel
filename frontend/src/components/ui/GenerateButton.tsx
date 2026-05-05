import { Sparkles, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

// ---------------------------------------------------------------------------
// GenerateButton — v3 视觉：紫色 accent 梯度 + glow 光晕
// ---------------------------------------------------------------------------

interface GenerateButtonProps {
  onClick: () => void;
  loading?: boolean;
  label?: string;
  className?: string;
  disabled?: boolean;
  layoutId?: string;
}

const ACTIVE_BG =
  "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))";
const LOADING_BG =
  "linear-gradient(135deg, oklch(0.66 0.08 295), oklch(0.58 0.07 295))";
const ACTIVE_SHADOW =
  "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)";

export function GenerateButton({
  onClick,
  loading = false,
  label = "生成",
  className,
  disabled = false,
  layoutId,
}: GenerateButtonProps) {
  const isDisabled = disabled || loading;

  return (
    <motion.button
      type="button"
      layout
      layoutId={layoutId}
      onClick={onClick}
      disabled={isDisabled}
      className={`focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12.5px] font-medium transition-transform ${
        isDisabled ? "cursor-not-allowed opacity-60" : ""
      } ${className ?? ""}`}
      style={{
        color: "oklch(0.14 0 0)",
        background: loading ? LOADING_BG : ACTIVE_BG,
        boxShadow: ACTIVE_SHADOW,
      }}
      animate={
        loading
          ? { opacity: [0.75, 1, 0.75] }
          : { opacity: isDisabled ? 0.6 : 1 }
      }
      whileHover={isDisabled ? undefined : { y: -1 }}
      transition={
        loading
          ? { duration: 1.5, repeat: Infinity, ease: "easeInOut" }
          : { duration: 0.3 }
      }
    >
      <AnimatePresence mode="wait" initial={false}>
        {loading ? (
          <motion.span
            key="loader"
            initial={{ opacity: 0, rotate: -90 }}
            animate={{ opacity: 1, rotate: 0 }}
            exit={{ opacity: 0, rotate: 90 }}
            transition={{ duration: 0.2 }}
          >
            <Loader2 className="h-4 w-4 animate-spin" />
          </motion.span>
        ) : (
          <motion.span
            key="sparkles"
            initial={{ opacity: 0, scale: 0.5 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.5 }}
            transition={{ duration: 0.2 }}
          >
            <Sparkles className="h-4 w-4" />
          </motion.span>
        )}
      </AnimatePresence>
      <span>{loading ? "生成中..." : label}</span>
    </motion.button>
  );
}
