// 溢出探针：找出「内容溢出但用户够不到」的元素，以及没有声明却出现的横向滚动。
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

export interface StrayScrollX {
  /** 出现横向滚动、却没有声明横向滚动的元素的选择器路径。 */
  path: string;
  client: { width: number };
  scroll: { width: number };
  /** 越出右缘的最深后代，用于定位真正撑宽内容的元素。 */
  culprit: string | null;
}

export interface LayoutReport {
  viewportWidth: number;
  viewportHeight: number;
  documentScrollWidth: number;
  documentScrollHeight: number;
  clipped: ClippedOverflow[];
  /**
   * 只写了纵向滚动（如 `overflow-y-auto`，弹层正文都是这样）的元素，浏览器把它的 overflow-x 也算成 auto：
   * 宽内容不会被裁切，而是让它横向滚动，clipped 查不到。这类横向滚动是缺陷，内容应当折行或限宽；
   * 确需横向滚动的区域（时间线轨道、代码块、标签带）写 `overflow-x-auto` 声明。
   */
  strayScrollX: StrayScrollX[];
}

export function inspectLayout(): LayoutReport {
  // 布局取整误差。
  const TOLERANCE = 1;
  const CLIPPING = new Set(["hidden", "clip"]);
  // 豁免必须写明原因：空值不算豁免。
  const EXEMPT = '[data-overflow-ok]:not([data-overflow-ok=""])';
  const SCROLLING = new Set(["auto", "scroll"]);
  // 声明横向滚动的工具类，含响应式与状态前缀。
  const SCROLL_X_CLASS = /^(?:[\w-]+:)*overflow-(?:x-)?(?:auto|scroll)$/;

  function describe(el: Element): string {
    let label = el.tagName.toLowerCase();
    if (el.id) label += `#${el.id}`;
    for (const attribute of ["data-testid", "data-slot"]) {
      const value = el.getAttribute(attribute);
      if (value) label += `[${attribute}="${value}"]`;
    }
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

  function declaresScrollX(el: Element): boolean {
    if (el instanceof HTMLElement && SCROLLING.has(el.style.overflowX)) return true;
    return Array.from(el.classList).some((name) => SCROLL_X_CLASS.test(name));
  }

  const clipped: ClippedOverflow[] = [];
  const strayScrollX: StrayScrollX[] = [];
  for (const el of [document.documentElement, ...Array.from(document.body.querySelectorAll("*"))]) {
    if (el.closest(EXEMPT)) continue;
    if (!isVisible(el)) continue;
    const style = getComputedStyle(el);
    if (
      el !== document.documentElement &&
      SCROLLING.has(style.overflowX) &&
      el.scrollWidth - el.clientWidth > TOLERANCE &&
      !(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) &&
      !declaresScrollX(el)
    ) {
      strayScrollX.push({
        path: pathOf(el),
        client: { width: el.clientWidth },
        scroll: { width: el.scrollWidth },
        culprit: findCulprit(el, true, false),
      });
    }
    const clipX = CLIPPING.has(style.overflowX);
    const clipY = CLIPPING.has(style.overflowY);
    if (!clipX && !clipY) continue;
    // sr-only 与折叠态元素。
    if (el.clientWidth <= 1 || el.clientHeight <= 1) continue;
    const overX = clipX && el.scrollWidth - el.clientWidth > TOLERANCE;
    const overY = clipY && el.scrollHeight - el.clientHeight > TOLERANCE;
    if (!overX && !overY) continue;
    if (overX && !overY && style.textOverflow === "ellipsis") continue;
    // 单行输入框的长值随光标横向滚动，键盘与指针都能到达。
    if (overX && !overY && el instanceof HTMLInputElement) continue;
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
    viewportWidth: document.documentElement.clientWidth,
    viewportHeight: window.innerHeight,
    documentScrollWidth: document.documentElement.scrollWidth,
    documentScrollHeight: document.documentElement.scrollHeight,
    clipped,
    strayScrollX,
  };
}
