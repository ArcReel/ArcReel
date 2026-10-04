import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * 页头第二行右侧的动作插槽。`undefined` 表示不在集页里，`null` 表示插槽节点还没挂上。
 */
const EpisodeHeaderSlotContext = createContext<HTMLElement | null | undefined>(undefined);

export const EpisodeHeaderSlotProvider = EpisodeHeaderSlotContext.Provider;

/**
 * 各视图往集页页头放当前视图的动作（批量补齐、编写提示词、出片等）。子元素经 portal 渲染到页头，
 * 状态与事件仍留在调用方的组件树里。一个视图只放一处，按当前视图条件渲染；
 * 不在集页里（如画布单独渲染的测试）时就地渲染。
 */
export function EpisodeHeaderActions({ children }: { children: ReactNode }) {
  const slot = useContext(EpisodeHeaderSlotContext);
  if (slot === undefined) return <>{children}</>;
  if (slot === null) return null;
  return createPortal(children, slot);
}
