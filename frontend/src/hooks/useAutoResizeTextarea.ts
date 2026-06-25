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
      // scrollHeight excludes the border, so under box-sizing: border-box the
      // element ends up 2px short and clips its last line. Add the border back.
      const style = window.getComputedStyle(el);
      const borderHeight =
        style.boxSizing === "border-box"
          ? parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)
          : 0;
      el.style.height = `${el.scrollHeight + borderHeight}px`;
    }
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  return { ref, resize };
}
