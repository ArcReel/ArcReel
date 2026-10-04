import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";

export const EditUnitRetentionContext = createContext<{
  protect: (value: boolean) => void;
  message?: string;
} | null>(null);

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
  const [protectedUnit, protect] = useState(false);
  const [shown, setShown] = useState({ identity, value });
  const retained = protectedUnit && shown.identity !== identity;
  if (!retained && (shown.identity !== identity || shown.value !== value)) {
    setShown({ identity, value });
  }
  const protectParent = parent?.protect;
  useLayoutEffect(() => {
    protectParent?.(protectedUnit);
    return () => protectParent?.(false);
  }, [protectedUnit, protectParent]);
  return (
    <EditUnitRetentionContext.Provider value={{ protect, message: retained ? message : parent?.message }}>
      {children(retained ? shown.value : value)}
    </EditUnitRetentionContext.Provider>
  );
}
