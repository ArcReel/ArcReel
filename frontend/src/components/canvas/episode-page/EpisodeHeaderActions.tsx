import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Action slot on the right of the header's second row. `undefined` means outside an episode page; `null` means its node has not mounted.
 */
const EpisodeHeaderSlotContext = createContext<HTMLElement | null | undefined>(undefined);

/** Episode page supplies the slot node; null before mount. */
export function EpisodeHeaderSlotProvider({ value, children }: { value: HTMLElement | null; children: ReactNode }) {
  return <EpisodeHeaderSlotContext.Provider value={value}>{children}</EpisodeHeaderSlotContext.Provider>;
}

/**
 * Views place current actions such as batch generation, prompt authoring and rendering in the episode header. Portal children into the header,
 * retaining state/events in the caller's component tree. Use one instance per view, rendered conditionally for the active view;
 * render in place outside episode pages, such as standalone canvas tests.
 */
export function EpisodeHeaderActions({ children }: { children: ReactNode }) {
  const slot = useContext(EpisodeHeaderSlotContext);
  if (slot === undefined) return <>{children}</>;
  if (slot === null) return null;
  return createPortal(children, slot);
}
