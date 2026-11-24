import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

const EpisodeHeaderSlotContext = createContext<HTMLElement | null | undefined>(undefined);

export function EpisodeHeaderSlotProvider({ value, children }: { value: HTMLElement | null; children: ReactNode }) {
  return <EpisodeHeaderSlotContext.Provider value={value}>{children}</EpisodeHeaderSlotContext.Provider>;
}

export function EpisodeHeaderActions({ children }: { children: ReactNode }) {
  const slot = useContext(EpisodeHeaderSlotContext);
  if (slot === undefined) return <>{children}</>;
  if (slot === null) return null;
  return createPortal(children, slot);
}
