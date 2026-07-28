import { useScriptGenerationNotice } from "@/hooks/useScriptGenerationNotice";

/**
 * 无 UI 监听组件：把 useScriptGenerationNotice 对 assistant store 的订阅隔离在叶子节点，
 * 避免直接挂在 StudioLayout 时助手流每次更新都触发整个布局重渲染。常驻挂载使其去重
 * 记录跨会话/项目切换持久。
 */
export function ScriptGenerationNoticeListener() {
  useScriptGenerationNotice();
  return null;
}
