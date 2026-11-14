import { useEffect, useRef, useState } from "react";

/**
 * 监听元素自身的可用宽度，窄于阈值时返回 true。
 *
 * 用于「同一段文字在窄容器里换一种写法」这类 CSS 做不到的切换：容器查询只能换样式，
 * 换不了文本内容。初始渲染、没有 ResizeObserver 的环境与测试环境（宽度为 0）都按
 * 「不窄」处理，桌面用例读到的仍是完整文案。
 */
export function useContainerNarrow<T extends HTMLElement>(threshold: number) {
  const ref = useRef<T | null>(null);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width;
      // 宽度为 0 表示尚未完成布局（隐藏、挂载首帧），不据此切换文案。
      if (width > 0) setNarrow(width < threshold);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold]);

  return [ref, narrow] as const;
}
