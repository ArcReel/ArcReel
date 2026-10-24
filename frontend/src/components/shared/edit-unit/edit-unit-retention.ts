import { createContext, useContext, useId, useLayoutEffect } from "react";

/**
 * 子树里的编辑单元经它报告「有未保存修改或保存在途」。同一视图里可能有多个单元（如资产与它的各条衍生），
 * 按 `key` 分别登记，任一单元在保护中视图就保留。
 */
export const EditUnitRetentionContext = createContext<{
  protect: (key: string, value: boolean) => void;
  message?: string;
} | null>(null);

/**
 * 编辑单元在 `active`（有未保存修改或保存在途）期间要求所在的 RetainedEditUnit 保留可见内容。
 * 同一视图里的多个保护者各自调用。返回所在的保留上下文，取其 `message` 判断是否已被外部替换。
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
