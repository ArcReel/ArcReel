import { createContext, useContext } from "react";

export const ArrivedFailuresContext = createContext<ReadonlySet<string>>(new Set());

export function useArrivedFailures(): ReadonlySet<string> {
  return useContext(ArrivedFailuresContext);
}
