import { useEffect, type RefObject } from "react";

const FOCUSABLE =
  'a[href], area[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("disabled") && el.tabIndex !== -1,
  );
}

/**
 * 将键盘焦点困在 ref 容器内（Tab / Shift+Tab 循环）。
 * 启用时把焦点移到容器内首个可聚焦元素；卸载时把焦点还给之前持有焦点的元素。
 * 例外：若 effect 触发时焦点已经在容器内（子组件在更深的 useEffect 里抢先调了
 * `someRef.current?.focus()`），保留它，避免反复夺焦。
 *
 * 关闭回焦：调用方可传 `returnTargetRef`，cleanup 时优先恢复 ref 内保存的目标。
 * useEffect 在 React 里是 bottom-up：子组件的 nameRef.focus() 先于本 hook 跑，
 * 所以直接读 `document.activeElement` 会拿到已被子组件改写的输入框节点，关闭
 * 后 focus 一个即将卸载的元素 → 焦点丢到 body。`returnTargetRef` 应在更上层
 * 的 render 阶段（open=false→true 的边沿）由 ModalShell 等容器写入真正的
 * trigger 节点。
 */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active = true,
  returnTargetRef?: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!active) return;
    const container = ref.current;
    if (!container) return;

    const fallbackReturnTarget = document.activeElement as HTMLElement | null;
    if (!container.contains(fallbackReturnTarget)) {
      const initial = getFocusable(container)[0] ?? container;
      initial.focus();
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const items = getFocusable(container);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const activeEl = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (activeEl === first || !container.contains(activeEl)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (activeEl === last || !container.contains(activeEl)) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    // ref 的 current 在 cleanup 跑时可能已被父组件覆盖；effect 内拷出来用。
    const explicitReturn = returnTargetRef?.current ?? null;
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      // 若调用方提供 returnTargetRef 但 ref 内尚未写入（首次开），fallback 到
      // 进入 effect 时观察到的 activeElement——这至少比 body 强。
      const target =
        explicitReturn
        ?? (container.contains(fallbackReturnTarget) ? null : fallbackReturnTarget);
      target?.focus?.();
    };
  }, [ref, active, returnTargetRef]);
}
