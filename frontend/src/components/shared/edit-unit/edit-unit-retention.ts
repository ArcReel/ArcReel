import { createContext, useContext, useId, useLayoutEffect } from "react";

/**
 * Descendant editing units report unsaved changes or in-flight saves here. One view may contain several units, such as an asset and its derivatives;
 * register each by `key`, retaining the view while any unit requires protection.
 */
export const EditUnitRetentionContext = createContext<{
  protect: (key: string, value: boolean) => void;
  message?: string;
} | null>(null);

/**
 * An editing unit requests its RetainedEditUnit preserve visible content while `active`, meaning unsaved changes or an in-flight save.
 * Multiple protectors in one view call independently. Return the enclosing retention context; inspect `message` for external replacement.
 */
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
