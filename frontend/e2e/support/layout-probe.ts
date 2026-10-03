// 溢出探针：找出「内容溢出但用户够不到」的元素。
// inspectLayout 经 page.evaluate 序列化到浏览器里执行，函数体必须自包含：
// 不引用模块级变量，辅助函数全部写在函数内部。

export interface ClippedOverflow {
  /** 裁切内容的元素（overflow 为 hidden 或 clip）的选择器路径。 */
  path: string;
  axis: "x" | "y" | "xy";
  client: { width: number; height: number };
  scroll: { width: number; height: number };
  /** 越出裁切框的最深后代，用于定位真正撑大内容的元素。 */
  culprit: string | null;
}

export interface LayoutReport {
  viewportHeight: number;
  documentScrollHeight: number;
  clipped: ClippedOverflow[];
}

export function inspectLayout(): LayoutReport {
  // 布局取整误差。
  const TOLERANCE = 1;
  const CLIPPING = new Set(["hidden", "clip"]);
  // 豁免必须写明原因：空值不算豁免。
  const EXEMPT = '[data-overflow-ok]:not([data-overflow-ok=""])';

  function describe(el: Element): string {
    let label = el.tagName.toLowerCase();
    if (el.id) label += `#${el.id}`;
    const testId = el.getAttribute("data-testid");
    if (testId) label += `[data-testid="${testId}"]`;
    const classes = Array.from(el.classList).slice(0, 3);
    if (classes.length > 0) label += `.${classes.join(".")}`;
    return label;
  }

  function pathOf(el: Element): string {
    const parts: string[] = [];
    for (let node: Element | null = el; node && node !== document.documentElement; node = node.parentElement) {
      parts.unshift(describe(node));
    }
    return parts.join(" > ") || "html";
  }

  function isVisible(el: Element): boolean {
    return el.checkVisibility({ visibilityProperty: true, opacityProperty: true });
  }

  function findCulprit(container: Element, overX: boolean, overY: boolean): string | null {
    const box = container.getBoundingClientRect();
    let deepest: Element | null = null;
    let deepestDepth = -1;
    const walk = (el: Element, depth: number) => {
      for (const child of Array.from(el.children)) {
        if (!isVisible(child)) continue;
        const rect = child.getBoundingClientRect();
        const escapes =
          (overX && rect.right - box.right > TOLERANCE) || (overY && rect.bottom - box.bottom > TOLERANCE);
        if (escapes && depth > deepestDepth) {
          deepest = child;
          deepestDepth = depth;
        }
        walk(child, depth + 1);
      }
    };
    walk(container, 0);
    return deepest ? pathOf(deepest) : null;
  }

  const clipped: ClippedOverflow[] = [];
  for (const el of [document.documentElement, ...Array.from(document.body.querySelectorAll("*"))]) {
    if (el.closest(EXEMPT)) continue;
    if (!isVisible(el)) continue;
    const style = getComputedStyle(el);
    const clipX = CLIPPING.has(style.overflowX);
    const clipY = CLIPPING.has(style.overflowY);
    if (!clipX && !clipY) continue;
    // sr-only 与折叠态元素。
    if (el.clientWidth <= 1 || el.clientHeight <= 1) continue;
    const overX = clipX && el.scrollWidth - el.clientWidth > TOLERANCE;
    const overY = clipY && el.scrollHeight - el.clientHeight > TOLERANCE;
    if (!overX && !overY) continue;
    if (overX && !overY && style.textOverflow === "ellipsis") continue;
    const lineClamp = style.getPropertyValue("-webkit-line-clamp");
    if (overY && !overX && lineClamp !== "" && lineClamp !== "none") continue;
    clipped.push({
      path: pathOf(el),
      axis: overX && overY ? "xy" : overX ? "x" : "y",
      client: { width: el.clientWidth, height: el.clientHeight },
      scroll: { width: el.scrollWidth, height: el.scrollHeight },
      culprit: findCulprit(el, overX, overY),
    });
  }

  return {
    viewportHeight: window.innerHeight,
    documentScrollHeight: document.documentElement.scrollHeight,
    clipped,
  };
}
