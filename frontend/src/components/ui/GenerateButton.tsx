import { Sparkles, Loader2 } from "lucide-react";

// ---------------------------------------------------------------------------
// GenerateButton
// ---------------------------------------------------------------------------

interface GenerateButtonProps {
  onClick: () => void;
  loading?: boolean;
  label?: string;
  className?: string;
  disabled?: boolean;
}

export function GenerateButton({
  onClick,
  loading = false,
  label = "生成",
  className,
  disabled = false,
}: GenerateButtonProps) {
  const isDisabled = disabled || loading;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isDisabled}
      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-all ${
        loading
          ? "animate-pulse bg-gradient-to-r from-indigo-600 to-fuchsia-600"
          : "bg-gradient-to-r from-indigo-500 to-fuchsia-500 hover:from-indigo-400 hover:to-fuchsia-400"
      } ${isDisabled ? "cursor-not-allowed opacity-50" : ""} ${className ?? ""}`}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Sparkles className="h-4 w-4" />
      )}
      <span>{loading ? "生成中..." : label}</span>
    </button>
  );
}
