/** 描述是否可用于生成：去掉两端空白后非空，与服务端判定一致。 */
export function hasUsableDescription(description: string | null | undefined): boolean {
  return typeof description === "string" && description.trim().length > 0;
}
