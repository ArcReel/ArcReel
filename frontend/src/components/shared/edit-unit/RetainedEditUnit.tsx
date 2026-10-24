import { useCallback, useContext, useId, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { EditUnitRetentionContext } from "./edit-unit-retention";

/** 外部事件要替换正在编辑的单元时，继续显示当前内容；保存/放弃后再采用真实状态。 */
export function RetainedEditUnit<T>({
  identity, value, message, children,
}: {
  identity: string;
  value: T;
  message: string;
  children: (value: T) => ReactNode;
}) {
  const parent = useContext(EditUnitRetentionContext);
  const key = useId();
  const [protectedKeys, setProtectedKeys] = useState<ReadonlySet<string>>(() => new Set());
  const protect = useCallback((unit: string, on: boolean) => {
    setProtectedKeys((prev) => {
      if (prev.has(unit) === on) return prev;
      const next = new Set(prev);
      if (on) next.add(unit);
      else next.delete(unit);
      return next;
    });
  }, []);
  const protectedUnit = protectedKeys.size > 0;
  const [shown, setShown] = useState({ identity, value });
  const retained = protectedUnit && shown.identity !== identity;
  if (!retained && (shown.identity !== identity || shown.value !== value)) {
    setShown({ identity, value });
  }
  const protectParent = parent?.protect;
  useLayoutEffect(() => {
    protectParent?.(key, protectedUnit);
    return () => protectParent?.(key, false);
  }, [protectedUnit, protectParent, key]);
  const shownMessage = retained ? message : parent?.message;
  const retention = useMemo(() => ({ protect, message: shownMessage }), [protect, shownMessage]);
  return (
    <EditUnitRetentionContext.Provider value={retention}>
      {children(retained ? shown.value : value)}
    </EditUnitRetentionContext.Provider>
  );
}
