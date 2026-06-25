import { useCallback, useEffect, useRef } from "react";

/**
 * Keeps a textarea sized to its content. Returns a ref to attach to the
 * `<textarea>` and a `resize` callback to wire on `onInput` for immediate
 * feedback while typing (before the controlled value round-trips).
 */
export function useAutoResizeTextarea(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const borderHeightRef = useRef<number | null>(null);

  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // scrollHeight excludes the border, so under box-sizing: border-box the
    // element ends up 2px short and clips its last line. The border width is
    // static, so measure it once and reuse it instead of forcing a style
    // recalc on every keystroke.
    if (borderHeightRef.current === null) {
      const style = window.getComputedStyle(el);
      borderHeightRef.current =
        style.boxSizing === "border-box"
          ? parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)
          : 0;
    }
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + borderHeightRef.current}px`;
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  return { ref, resize };
}
