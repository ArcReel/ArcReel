import { useEffect, type RefObject } from "react";

/** 焦点在可输入的控件里：J / K 是要输入的字符，不当作快捷键。 */
function isTypingTarget(target: Element): boolean {
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  if (target instanceof HTMLInputElement) {
    return !["button", "checkbox", "radio", "range", "reset", "submit", "color", "file"].includes(target.type);
  }
  if (target instanceof HTMLElement && target.isContentEditable) return true;
  const role = target.getAttribute("role");
  return role === "textbox" || role === "combobox" || role === "searchbox";
}

export function isApplePlatform(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);
}

interface ShotShortcutsOptions {
  /** 快捷键只在焦点位于这个容器内（或页面没有焦点元素）时生效，不抢 Agent 面板、页头与弹层的按键。 */
  rootRef: RefObject<HTMLElement | null>;
  /** 回调需传稳定引用（useCallback）。 */
  onPrev: () => void;
  onNext: () => void;
  /** ⌘S / Ctrl+S；不传时不拦截浏览器的保存快捷键。 */
  onSave?: () => void;
  /** 切换分镜暂不可用（改序、增删在途）。 */
  navDisabled: boolean;
}

/**
 * 分镜视图的快捷键：J / K 切到下一个与上一个分镜，焦点在输入框里时不响应；⌘S（Windows 为 Ctrl+S）保存当前分镜，
 * 在输入框里也可用。弹层渲染在容器之外，打开时不响应；输入法组合输入中的按键与带其他修饰键的组合都放行。
 */
export function useShotShortcuts({ rootRef, onPrev, onNext, onSave, navDisabled }: ShotShortcutsOptions): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.key === "Process") return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      const inScope = target === document.body || Boolean(rootRef.current?.contains(target));
      if (!inScope) return;
      const key = event.key.toLowerCase();

      if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && key === "s") {
        if (!onSave) return;
        event.preventDefault();
        onSave();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (key !== "j" && key !== "k") return;
      if (isTypingTarget(target) || navDisabled) return;
      event.preventDefault();
      if (key === "j") onNext();
      else onPrev();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [rootRef, onSave, onNext, onPrev, navDisabled]);
}
