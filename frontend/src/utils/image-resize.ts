/**
 * 复刻 Anthropic 官方参考实现：模型在处理前把图片缩到同时满足边长与视觉 token 两条
 * 限制的最大等比尺寸。见 https://platform.claude.com/docs/en/build-with-claude/vision-coordinates
 */

/** 视觉 token 数：每 28×28 像素一块，一块 1 个 token。 */
function countImageTokens(width: number, height: number): number {
  return Math.ceil(width / 28) * Math.ceil(height / 28);
}

/**
 * 四舍六入五取偶（banker's rounding），与 Python 的 `round()` 一致。
 * 服务端把恰好 .5 的短边解析为偶数邻值，`Math.round` 一律进位，两者对部分图片
 * 会算出相差 1 像素的尺寸。
 */
function roundTiesToEven(value: number): number {
  const floor = Math.floor(value);
  if (value - floor !== 0.5) return Math.round(value);
  return floor % 2 === 0 ? floor : floor + 1;
}

/**
 * 模型缩放（补边之前）后的像素尺寸，返回 `[width, height]`。
 *
 * 默认参数是标准分辨率档。已经落在两条限制内的图片原样返回。
 */
export function resizedSize(
  width: number,
  height: number,
  maxEdge = 1568,
  maxTokens = 1568,
): [number, number] {
  const fits = (w: number, h: number): boolean =>
    Math.ceil(w / 28) * 28 <= maxEdge
    && Math.ceil(h / 28) * 28 <= maxEdge
    && countImageTokens(w, h) <= maxTokens;

  if (fits(width, height)) return [width, height];
  if (height > width) {
    const [resizedH, resizedW] = resizedSize(height, width, maxEdge, maxTokens);
    return [resizedW, resizedH];
  }

  // 沿长边二分：找出仍然合规的最大等比尺寸。
  const aspectRatio = width / height;
  let lo = 1; // lo 恒合规
  let hi = width; // hi 恒不合规
  while (lo + 1 < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (fits(mid, Math.max(roundTiesToEven(mid / aspectRatio), 1))) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return [lo, Math.max(roundTiesToEven(lo / aspectRatio), 1)];
}
