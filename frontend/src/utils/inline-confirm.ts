import type { FocusEvent, KeyboardEvent, PointerEvent } from "react";


type Dismiss = (returnFocus: boolean) => void;

export function dismissOnEscape(event: KeyboardEvent, dismiss: Dismiss) {
  if (event.key !== "Escape") return;
  event.stopPropagation();
  dismiss(true);
}

export function dismissOnFocusLeave(event: FocusEvent<HTMLElement>, dismiss: Dismiss) {
  const next = event.relatedTarget;
  if (next instanceof Node ? event.currentTarget.contains(next) : !document.hasFocus()) return;
  dismiss(false);
}

export function keepFocusInside(event: PointerEvent<HTMLElement>) {
  if (event.target instanceof Element && event.target.closest("button")) return;
  event.preventDefault();
}
