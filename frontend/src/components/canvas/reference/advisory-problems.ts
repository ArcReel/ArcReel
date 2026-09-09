import type { ReferenceDurationPrecheck, ReferenceProjectionProblem } from "@/types";

/**
 * 预检里不拦截入队、但用户有权在入队前知道的问题（当前只有参考图取前 N 张）。
 *
 * 判定收在这一处：闸门据此决定要不要开确认框，弹窗据此决定要不要画告知分区。两处问的是
 * 同一件事，各写一遍就会在改判时静默失配——闸门开框而弹窗渲染空，用户既确认不了也取消不了。
 *
 * 没有 `message` 的条目跳过：可读文本由服务端按当前语言渲染，缺它就没有可展示的内容。
 */
export function advisoryProblems(
  precheck: ReferenceDurationPrecheck,
): ReferenceProjectionProblem[] {
  return precheck.problems.filter((problem) => !problem.blocking && Boolean(problem.message));
}
