import { useRef, useEffect, useCallback } from "react";

interface AutoTextareaProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}

/** Auto-resizing textarea that grows with its content. */
export function AutoTextarea({
  value,
  onChange,
  placeholder,
  className,
}: AutoTextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  const resize = useCallback(() => {
    const el = ref.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onInput={resize}
      placeholder={placeholder}
      rows={2}
      className={`w-full resize-none overflow-hidden bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-2 font-mono text-xs text-gray-200 placeholder-gray-500 focus:border-indigo-500 focus:outline-none ${className ?? ""}`}
    />
  );
}
