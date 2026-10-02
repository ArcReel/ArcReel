import { useLayoutEffect, type RefObject } from "react";

const FOCUSABLE =
  'a[href], area[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("disabled") && el.tabIndex !== -1,
  );
}

// 按激活顺序排列的已激活 trap；只有栈顶（最后激活的）处理 Tab，叠在上层的对话框关闭后由下层接管。
const activeTraps: object[] = [];

/**
 * 将键盘焦点困在 ref 容器内（Tab / Shift+Tab 循环）。
 * 多个 trap 同时激活时（对话框上再弹确认框），只有最后激活的那个处理 Tab。
 * 启用时把焦点移到容器内首个可聚焦元素；卸载时把焦点还给之前持有焦点的元素。
 * 初始聚焦在 layout effect 里同步完成：对话框一出现在 DOM 里，焦点就已经落进容器，
 * 不会留下「已渲染、焦点未落定」的窗口（passive effect 可能晚于 DOM 变化才执行）。
 * 例外：若 effect 触发时焦点已经在容器内（子组件在更深的 layout effect 里抢先调了
 * `someRef.current?.focus()`），保留它，避免反复夺焦；子组件在 useEffect 里调的 focus 晚于本 hook，直接覆盖初始聚焦。
 *
 * 关闭回焦：调用方可传 `returnTargetRef`，cleanup 时优先恢复 ref 内保存的目标。
 * layout effect 在 React 里是 bottom-up：子组件的 `autoFocus` 与更深层 layout effect 里的
 * focus 先于本 hook 跑，所以直接读 `document.activeElement` 可能拿到已被子组件改写的输入框节点，关闭
 * 后 focus 一个即将卸载的元素 → 焦点丢到 body。`returnTargetRef` 应在更上层
 * 的 render 阶段（open=false→true 的边沿）由 ModalShell 等容器写入真正的
 * trigger 节点。
 */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active = true,
  returnTargetRef?: RefObject<HTMLElement | null>,
) {
  useLayoutEffect(() => {
    if (!active) return;
    const container = ref.current;
    if (!container) return;

    const fallbackReturnTarget = document.activeElement as HTMLElement | null;
    if (!container.contains(fallbackReturnTarget)) {
      const initial = getFocusable(container)[0] ?? container;
      initial.focus();
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || activeTraps.at(-1) !== handleKeyDown) return;
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
    activeTraps.push(handleKeyDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      activeTraps.splice(activeTraps.indexOf(handleKeyDown), 1);
      // 若调用方提供 returnTargetRef 但 ref 内尚未写入（首次开），fallback 到
      // 进入 effect 时观察到的 activeElement——这至少比 body 强。
      const target =
        explicitReturn
        ?? (container.contains(fallbackReturnTarget) ? null : fallbackReturnTarget);
      target?.focus?.();
    };
  }, [ref, active, returnTargetRef]);
}
