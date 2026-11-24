import { useEffect, type RefObject } from "react";

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
  rootRef: RefObject<HTMLElement | null>;
  onPrev: () => void;
  onNext: () => void;
  onSave?: () => void;
  navDisabled: boolean;
}

export function useShotShortcuts({ rootRef, onPrev, onNext, onSave, navDisabled }: ShotShortcutsOptions): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.key === "Process") return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      const inScope = target === document.body || Boolean(rootRef.current?.contains(target));
      const key = event.key.toLowerCase();

      if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && key === "s") {
        if (!onSave) return;
        event.preventDefault();
        if (inScope) onSave();
        return;
      }
      if (!inScope) return;
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
