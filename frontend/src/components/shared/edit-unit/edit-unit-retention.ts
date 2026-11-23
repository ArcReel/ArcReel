import { createContext, useContext, useId, useLayoutEffect } from "react";

export const EditUnitRetentionContext = createContext<{
  protect: (key: string, value: boolean) => void;
  message?: string;
} | null>(null);

export function useRetainWhile(active: boolean) {
  const retention = useContext(EditUnitRetentionContext);
  const protect = retention?.protect;
  const key = useId();
  useLayoutEffect(() => {
    protect?.(key, active);
    return () => protect?.(key, false);
  }, [protect, key, active]);
  return retention;
}
