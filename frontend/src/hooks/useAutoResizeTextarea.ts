import { useCallback, useEffect, useRef } from "react";

/**
 * Keeps a textarea sized to its content. Returns a ref to attach to the
 * `<textarea>` and a `resize` callback to wire on `onInput` for immediate
 * feedback while typing (before the controlled value round-trips).
 */
export function useAutoResizeTextarea(value: string) {
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

  return { ref, resize };
}
