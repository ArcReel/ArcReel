// 倍率：非负有限数。只认十进制写法（「1.5」「0.8」「.5」），科学计数、负号与其他字符一律
// 视为非法，返回 undefined 由保存拦截——不用 parseFloat 静默截断（「1.5x」→1.5）。
const DECIMAL_RE = /^(?:\d+(?:\.\d*)?|\.\d+)$/;

export function parseMultiplier(s: string): number | undefined {
  const trimmed = s.trim();
  if (!DECIMAL_RE.test(trimmed)) return undefined;
  const n = Number(trimmed);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}
