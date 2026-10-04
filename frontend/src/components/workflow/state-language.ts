/**
 * 工作流面板的状态色调。颜色只在同一条轴内部区分程度：任务（描边胶囊）里进行中用强调色，
 * 终态失败用危险色，其余中性。未登记的状态词不着色，绝不在查表上崩掉整个面板。
 */
export function taskToneClass(status: string): string {
  if (status === "queued" || status === "running" || status === "succeeded") return "border-primary/30 text-primary";
  if (status === "failed" || status === "interrupted") return "border-destructive/40 text-destructive";
  return "border-input text-muted-foreground";
}
