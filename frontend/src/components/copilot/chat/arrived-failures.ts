import { createContext, useContext } from "react";

/**
 * UUIDs of turn-failure entries arriving while viewed, supplied by MessageFlow: announce only these cards; existing failures remain visible
 * without announcements on opening a session. Subagent cards use this for nested failures; do not announce existing failures merely revealed by expansion.
 */
export const ArrivedFailuresContext = createContext<ReadonlySet<string>>(new Set());

export function useArrivedFailures(): ReadonlySet<string> {
  return useContext(ArrivedFailuresContext);
}
