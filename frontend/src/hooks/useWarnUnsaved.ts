import { useEffect } from "react";

/**
 * 当有未保存更改时，阻止用户关闭/刷新标签页。
 */
export function useWarnUnsaved(isDirty: boolean) {
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);
}
