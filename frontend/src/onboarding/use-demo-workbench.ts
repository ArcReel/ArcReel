/**
 * 「当前打开的是演示项目」这一判定的单一来源。
 *
 * 工作台里绝大多数控件靠既有惯用法关掉（不传写回调 ⇒ 入口不渲染），少数需要跨层知道自己
 * 处在演示态的位置（布局要不要连 SSE、侧栏要不要拉文件列表、工具栏要不要给「新建」）用这个
 * hook 从 store 派生。判定只看当前项目名，离开演示项目即自动失效，不需要额外的开关状态。
 */

import { useProjectsStore } from "@/stores/projects-store";
import { isDemoProject } from "./demo-project";

/** 当前工作台是否处在只读的演示态。 */
export function useDemoWorkbench(): boolean {
  return useProjectsStore((s) => isDemoProject(s.currentProjectName));
}
