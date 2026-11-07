// Overflow probes identify inaccessible overflow and undeclared horizontal scrolling.
// inspectLayout serializes through page.evaluate into browsers, requiring self-contained bodies:
// no module-level references; all helpers live inside functions.

export interface ClippedOverflow {
  /** Selector paths for elements clipping contents through hidden/clip overflow. */
  path: string;
  axis: "x" | "y" | "xy";
  client: { width: number; height: number };
  scroll: { width: number; height: number };
  /** Deepest descendants exceeding clipping bounds, locating actual oversized contents. */
  culprit: string | null;
}

export interface StrayScrollX {
  /** Selector paths for horizontal scrolling without declared horizontal scroll intent. */
  path: string;
  client: { width: number };
  scroll: { width: number };
  /** Deepest descendants exceeding right edges, locating width-expanding content. */
  culprit: string | null;
}

export interface LayoutReport {
  viewportWidth: number;
  viewportHeight: number;
  documentScrollWidth: number;
  documentScrollHeight: number;
  clipped: ClippedOverflow[];
  /**
   * Vertical-only scrolling (for example `overflow-y-auto` overlay bodies) also computes overflow-x auto:
   * wide content scrolls rather than clips, invisible to clipped probes. Such horizontal scrolling is defective; wrap/constrain contents.
   * Genuine horizontal regions (timeline tracks/code/tabs) declare `overflow-x-auto`.
   */
  strayScrollX: StrayScrollX[];
}

export function inspectLayout(): LayoutReport {
  // Layout-rounding tolerance.
  const TOLERANCE = 1;
  const CLIPPING = new Set(["hidden", "clip"]);
  // Exemptions require reasons; empty values are not exemptions.
  const EXEMPT = '[data-overflow-ok]:not([data-overflow-ok=""])';
  const SCROLLING = new Set(["auto", "scroll"]);
  // Declared horizontal-scroll utility classes, including responsive/state prefixes.
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
    // sr-only and collapsed elements.
    if (el.clientWidth <= 1 || el.clientHeight <= 1) continue;
    const overX = clipX && el.scrollWidth - el.clientWidth > TOLERANCE;
    const overY = clipY && el.scrollHeight - el.clientHeight > TOLERANCE;
    if (!overX && !overY) continue;
    if (overX && !overY && style.textOverflow === "ellipsis") continue;
    // Long single-line input values scroll horizontally with carets, reachable by keyboard/pointer.
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
