import { Sparkles, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

// ---------------------------------------------------------------------------
// GenerateButton — 带 framer-motion 平滑状态过渡的生成按钮
// ---------------------------------------------------------------------------

interface GenerateButtonProps {
  onClick: () => void;
  loading?: boolean;
  label?: string;
  className?: string;
  disabled?: boolean;
  layoutId?: string;
}

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
      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-colors ${
        loading
          ? "bg-indigo-700"
          : "bg-indigo-600 hover:bg-indigo-500"
      } ${isDisabled ? "cursor-not-allowed opacity-50" : ""} ${className ?? ""}`}
      animate={
        loading
          ? { opacity: [0.7, 1, 0.7] }
          : { opacity: isDisabled ? 0.5 : 1 }
      }
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
