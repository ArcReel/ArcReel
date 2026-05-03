/**
 * supported_durations 输入 / 显示 / 检测的纯函数工具。
 *
 * 设计：所有持久化形态都是 list[int]。本工具负责 UI ↔ list 互转、
 * 检测连续整数集（用于 slider/按钮组渲染选择）、以及格式化展示标签。
 */

const MAX_RANGE_SPAN = 30;

/**
 * 解析用户输入的逗号分隔时长文本，支持区间简写。
 *
 * 规则：
 *   - 逗号分隔片段；每段 trim
 *   - 单值：^\d+$（必须正整数）
 *   - 区间：^(\d+)-(\d+)$；min ≤ max 且跨度 ≤ MAX_RANGE_SPAN
 *   - 输出去重升序
 * @returns 解析得到的 list；输入为空白则 null
 * @throws Error 当存在非法片段
 */
export function parseDurationInput(text: string): number[] | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const segments = trimmed.split(",").map((s) => s.trim()).filter(Boolean);
  const result = new Set<number>();

  for (const seg of segments) {
    if (/^\d+$/.test(seg)) {
      const n = parseInt(seg, 10);
      if (n <= 0) throw new Error(`非法片段 '${seg}'：必须是正整数`);
      result.add(n);
      continue;
    }
    const m = /^(\d+)-(\d+)$/.exec(seg);
    if (m) {
      const lo = parseInt(m[1], 10);
      const hi = parseInt(m[2], 10);
      if (lo <= 0 || hi <= 0) throw new Error(`非法片段 '${seg}'：必须是正整数`);
      if (hi < lo) throw new Error(`非法片段 '${seg}'：区间右端必须 ≥ 左端`);
      if (hi - lo > MAX_RANGE_SPAN) {
        throw new Error(`非法片段 '${seg}'：区间过大（>${MAX_RANGE_SPAN}）`);
      }
      for (let i = lo; i <= hi; i++) result.add(i);
      continue;
    }
    throw new Error(`无法解析片段 '${seg}'`);
  }

  return [...result].sort((a, b) => a - b);
}

/** 判断列表是否为连续整数集（如 [3,4,5,6,7]），需 ≥2 个元素。 */
export function isContinuousIntegerRange(durations: readonly number[]): boolean {
  if (durations.length < 2) return false;
  const sorted = [...durations].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] !== sorted[i - 1] + 1) return false;
  }
  return true;
}

/**
 * 把 list[int] 紧凑展示，连续段折叠为 "min-max"。
 *
 * 例：[3,4,5,7,8,9,10,12] → "3-5, 7-10, 12"
 */
export function compactRangeFormat(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  const sorted = [...new Set(durations)].sort((a, b) => a - b);
  const parts: string[] = [];
  let runStart = sorted[0];
  let runPrev = sorted[0];
  for (let i = 1; i < sorted.length; i++) {
    const v = sorted[i];
    if (v === runPrev + 1) {
      runPrev = v;
    } else {
      parts.push(runStart === runPrev ? `${runStart}` : `${runStart}-${runPrev}`);
      runStart = v;
      runPrev = v;
    }
  }
  parts.push(runStart === runPrev ? `${runStart}` : `${runStart}-${runPrev}`);
  return parts.join(", ");
}

/** UI 标签格式：连续区间 → "3-7s"，否则 → "4, 6, 8s"。 */
export function formatDurationsLabel(durations: readonly number[]): string {
  if (durations.length === 0) return "";
  if (isContinuousIntegerRange(durations)) {
    const sorted = [...durations].sort((a, b) => a - b);
    return `${sorted[0]}-${sorted[sorted.length - 1]}s`;
  }
  return `${[...durations].sort((a, b) => a - b).join(", ")}s`;
}
