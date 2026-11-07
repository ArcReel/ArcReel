import type { FocusEvent, KeyboardEvent, PointerEvent } from "react";

// Inline-confirm exit events (docs/standards/frontend.md Inline confirmation). Callers choose initial/restored focus.

type Dismiss = (returnFocus: boolean) => void;

/** Bind buttons within regions: noninteractive containers cannot have keyboard handlers under jsx-a11y; focus departure ends confirmation, so Esc never originates outside. */
export function dismissOnEscape(event: KeyboardEvent, dismiss: Dismiss) {
  if (event.key !== "Escape") return;
  // Stop Esc bubbling from closing containing overlays.
  event.stopPropagation();
  dismiss(true);
}

/** Exit on focus departure, leaving focus at chosen destinations. */
export function dismissOnFocusLeave(event: FocusEvent<HTMLElement>, dismiss: Dismiss) {
  const next = event.relatedTarget;
  // Retain confirmation while focus stays inside, or windows blur (null relatedTarget and unfocused documents).
  if (next instanceof Node ? event.currentTarget.contains(next) : !document.hasFocus()) return;
  dismiss(false);
}

/** Notice-text clicks retain focus off body; buttons and their icon/text nodes receive focus normally. */
export function keepFocusInside(event: PointerEvent<HTMLElement>) {
  if (event.target instanceof Element && event.target.closest("button")) return;
  event.preventDefault();
}
