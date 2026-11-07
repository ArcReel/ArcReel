import { describe, expect, it, vi } from "vitest";
import { ONBOARDING_ANCHORS } from "./anchors";
import { anchorSelector, startTour, type TourLabels, type TourStep } from "./tour";

const LABELS: TourLabels = {
  next: "继续",
  prev: "上一步",
  done: "完成",
  skip: "跳过",
  close: "关闭引导",
  progress: (current, total) => `第 ${current} 步，共 ${total} 步`,
};

const TWO_STEPS: TourStep[] = [
  { anchor: null, title: "欢迎", body: "开场" },
  { anchor: null, title: "轮到你了", body: "收尾" },
];

function popover(): HTMLElement {
  const el = document.querySelector<HTMLElement>(".driver-popover");
  if (!el) throw new Error("popover not rendered");
  return el;
}

function click(selector: string): void {
  const el = popover().querySelector<HTMLElement>(selector);
  if (!el) throw new Error(`${selector} not found`);
  el.click();
}

describe("anchorSelector", () => {
  it("maps an anchor name to its data-onboarding selector", () => {
    expect(anchorSelector(ONBOARDING_ANCHORS.lobbyCreateProject)).toBe(
      '[data-onboarding="lobby-create-project"]',
    );
  });
});

describe("startTour", () => {
  it("renders a centered popover with no highlighted element for anchor: null", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".driver-popover-title")?.textContent).toBe("欢迎");
    // No real elements are highlighted; driver uses its placeholders and centers bubbles.
    expect(document.querySelector(".driver-active-element")?.id).toBe("driver-dummy-element");

    handle.dispose();
  });

  it("opens at the requested step and reports where it stands", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), startIndex: 1 });

    expect(popover().querySelector(".driver-popover-title")?.textContent).toBe("轮到你了");
    expect(handle.currentIndex()).toBe(1);

    handle.dispose();
  });

  it("clamps an out-of-range start index onto the last step", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), startIndex: 9 });

    expect(handle.currentIndex()).toBe(1);

    handle.dispose();
  });

  it("drops driver's own transitions when the user asks for reduced motion", () => {
    // driver encodes animation as body driver-fade/driver-simple alternatives.
    const matchMedia = vi.fn((query: string) => ({ matches: query.includes("reduce") }) as MediaQueryList);
    vi.stubGlobal("matchMedia", matchMedia);

    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(document.body).toHaveClass("driver-simple");
    expect(document.body).not.toHaveClass("driver-fade");

    handle.dispose();
    vi.unstubAllGlobals();
  });

  it("keeps driver's transitions when reduced motion is not requested", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(document.body).toHaveClass("driver-fade");

    handle.dispose();
  });

  it("uses the anchor's element when an anchor name is given", () => {
    const target = document.createElement("div");
    target.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyCreateProject);
    document.body.appendChild(target);

    const handle = startTour(
      [{ anchor: ONBOARDING_ANCHORS.lobbyCreateProject, title: "入口", body: "在这里新建" }],
      LABELS,
      { onExit: vi.fn() },
    );

    expect(target).toHaveClass("driver-active-element");

    handle.dispose();
    target.remove();
  });

  it("leaves the highlighted element's own ARIA state as it was, during the step and after", async () => {
    // Highlight dropdown triggers; driver adds aria-haspopup="dialog" and related attributes, deleting entire groups on departure.
    const trigger = document.createElement("button");
    trigger.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyCreateProject);
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    document.body.appendChild(trigger);

    const handle = startTour(
      [
        { anchor: ONBOARDING_ANCHORS.lobbyCreateProject, title: "入口", body: "在这里新建" },
        { anchor: null, title: "收尾", body: "讲完了" },
      ],
      LABELS,
      { onExit: vi.fn() },
    );

    await vi.waitFor(() => expect(trigger).toHaveAttribute("aria-haspopup", "menu"));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).not.toHaveAttribute("aria-controls");

    click(".driver-popover-next-btn");
    await vi.waitFor(() => expect(popover().querySelector(".driver-popover-title")?.textContent).toBe("收尾"));
    await vi.waitFor(() => expect(trigger).toHaveAttribute("aria-haspopup", "menu"));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    // Centered-bubble placeholders are empty divs, also without these attributes.
    await vi.waitFor(() => expect(document.getElementById("driver-dummy-element")).not.toHaveAttribute("aria-expanded"));

    handle.dispose();
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    trigger.remove();
  });

  it("moves the highlight along when content rendered above pushes the anchor down", async () => {
    // Returning lobbies render greetings after Example projects, pushing anchors down; highlights must follow.
    const target = document.createElement("div");
    target.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyDemoCard);
    document.body.appendChild(target);
    let top = 100;
    vi.spyOn(target, "getBoundingClientRect").mockImplementation(() => new DOMRect(40, top, 300, 200));
    const stage = () => document.querySelector(".driver-overlay path")?.getAttribute("d") ?? "";
    // Disable driver transitions so initial highlights settle; transition movement already follows anchors frame by frame.
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce") }) as MediaQueryList);

    const handle = startTour([{ anchor: ONBOARDING_ANCHORS.lobbyDemoCard, title: "演示卡", body: "长这样" }], LABELS, {
      onExit: vi.fn(),
    });
    // Highlight top=anchor top-stagePadding(8).
    await vi.waitFor(() => expect(stage()).toContain(",92 h"));

    top = 166;
    document.body.prepend(document.createElement("h1"));
    await vi.waitFor(() => expect(stage()).toContain(",158 h"));

    handle.dispose();
    target.remove();
    vi.unstubAllGlobals();
  });

  it("re-places the popover when the anchor is pushed down before the highlight settles", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce") }) as MediaQueryList);
    const handle = startTour(
      [
        { anchor: null, title: "欢迎", body: "开场" },
        { anchor: ONBOARDING_ANCHORS.lobbyDemoCard, title: "演示卡", body: "长这样" },
      ],
      LABELS,
      { onExit: vi.fn() },
    );
    const stage = () => document.querySelector(".driver-overlay path")?.getAttribute("d") ?? "";
    // Backdrops mount at first steps without reinsertion afterward.
    await vi.waitFor(() => expect(stage()).not.toBe(""));
    click(".driver-popover-next-btn");

    const target = document.createElement("div");
    target.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyDemoCard);
    let top = 100;
    vi.spyOn(target, "getBoundingClientRect").mockImplementation(() => new DOMRect(40, top, 300, 200));
    // After anchors mount, driver immediately positions bubbles and highlights settle next frame; greetings render between them.
    document.body.appendChild(target);
    await Promise.resolve();
    expect(popover().querySelector(".driver-popover-title")?.textContent).toBe("演示卡");
    top = 166;
    document.body.prepend(document.createElement("h1"));

    await vi.waitFor(() => expect(stage()).toContain(",158 h"));
    // Bubbles sit below anchors; driver writes distances to viewport bottoms: height - (anchor bottom + stagePadding 8 + default gap 10).
    await vi.waitFor(() => expect(popover()).toHaveStyle({ bottom: `${window.innerHeight - (166 + 200 + 18)}px` }));

    handle.dispose();
    target.remove();
    vi.unstubAllGlobals();
  });

  it("falls back to a centered popover when the anchor is missing, and warns", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const handle = startTour(
      [{ anchor: ONBOARDING_ANCHORS.lobbyDemoCard, title: "演示卡", body: "长这样" }],
      LABELS,
      // Minimize waiting windows; real values allow cross-page navigation, while this tests post-timeout fallback only.
      { onExit: vi.fn(), anchorWaitMs: 10 },
    );

    await vi.waitFor(() => {
      expect(document.querySelector(".driver-popover-title")?.textContent).toBe("演示卡");
    });
    // Explanations continue, losing highlights only; driver placeholders return bubbles to centers.
    expect(document.querySelector(".driver-active-element")?.id).toBe("driver-dummy-element");
    expect(warn).toHaveBeenCalledWith(expect.stringContaining(ONBOARDING_ANCHORS.lobbyDemoCard));

    handle.dispose();
    warn.mockRestore();
  });

  it("renders one filmstrip cell per step, filled up to the current step", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    const cells = () => Array.from(popover().querySelectorAll<HTMLElement>(".arc-tour-filmstrip span"));
    expect(cells().map((c) => c.dataset.on)).toEqual(["1", "0"]);

    click(".driver-popover-next-btn");
    expect(cells().map((c) => c.dataset.on)).toEqual(["1", "1"]);

    handle.dispose();
  });

  it("states the step position in text for screen readers", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".arc-tour-sr-only")?.textContent).toBe("第 1 步，共 2 步");

    handle.dispose();
  });

  it("labels the close button", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".driver-popover-close-btn")?.getAttribute("aria-label")).toBe("关闭引导");

    handle.dispose();
  });

  it("offers skip on every step but the last", () => {
    const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

    expect(popover().querySelector(".arc-tour-skip-btn")?.textContent).toBe("跳过");

    click(".driver-popover-next-btn");
    expect(popover().querySelector(".arc-tour-skip-btn")).toBeNull();

    handle.dispose();
  });

  it("reports the exit once when the tour is skipped", () => {
    const onExit = vi.fn();
    startTour(TWO_STEPS, LABELS, { onExit });

    click(".arc-tour-skip-btn");

    expect(onExit).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".driver-popover")).toBeNull();
  });

  it("reports the exit once when the tour is closed", () => {
    const onExit = vi.fn();
    startTour(TWO_STEPS, LABELS, { onExit });

    click(".driver-popover-close-btn");

    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("reports the exit once when the tour is played to the end", () => {
    const onExit = vi.fn();
    startTour(TWO_STEPS, LABELS, { onExit });

    click(".driver-popover-next-btn");
    click(".driver-popover-next-btn");

    expect(onExit).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".driver-popover")).toBeNull();
  });

  it("hands focus back to where it was before the tour once the tour exits", () => {
    // Tours begin with open dialogs/input focus; exiting returns focus there rather than body.
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();

    startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });
    // Browsers let driver focus bubbles; layoutless jsdom considers buttons invisible, so move focus for it here.
    popover().querySelector<HTMLElement>(".driver-popover-next-btn")?.focus();
    click(".driver-popover-next-btn");
    click(".driver-popover-close-btn");

    expect(input).toHaveFocus();
    input.remove();
  });

  it("does not report an exit when the caller disposes the tour", () => {
    const onExit = vi.fn();
    const handle = startTour(TWO_STEPS, LABELS, { onExit });

    handle.dispose();

    expect(onExit).not.toHaveBeenCalled();
    expect(document.querySelector(".driver-popover")).toBeNull();
  });

  describe("keyboard navigation", () => {
    // Built-in driver arrows bypass onNextClick/onPrevClick and onStepChange reporting
    // (breaking cross-page navigation); startTour disables them and handles Esc/arrows itself. Assert keyboard paths
    // share button-click reporting logic.
    it("advances and reports onStepChange on ArrowRight, same as clicking next", () => {
      const onStepChange = vi.fn();
      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), onStepChange });

      window.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowRight" }));

      expect(onStepChange).toHaveBeenCalledWith(1);
      expect(handle.currentIndex()).toBe(1);

      handle.dispose();
    });

    it("finishes the tour on ArrowRight at the last step, same as clicking next", () => {
      const onExit = vi.fn();
      const onStepChange = vi.fn();
      startTour(TWO_STEPS, LABELS, { onExit, startIndex: 1, onStepChange });

      window.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowRight" }));

      expect(onStepChange).not.toHaveBeenCalled();
      expect(onExit).toHaveBeenCalledTimes(1);
      expect(document.querySelector(".driver-popover")).toBeNull();
    });

    it("moves back and reports onStepChange on ArrowLeft", () => {
      const onStepChange = vi.fn();
      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), startIndex: 1, onStepChange });

      window.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowLeft" }));

      expect(onStepChange).toHaveBeenCalledWith(0);
      expect(handle.currentIndex()).toBe(0);

      handle.dispose();
    });

    it("ignores ArrowLeft on the first step instead of exiting the tour", () => {
      // First-step Previous buttons disable click(), but keyboard paths lack this disabled
      // state; without interception, driver.js movePrevious() finds no prior steps and destroys tours,
      // prematurely exiting without Skip/Close/Finish actions.
      const onExit = vi.fn();
      const onStepChange = vi.fn();
      const handle = startTour(TWO_STEPS, LABELS, { onExit, onStepChange });

      window.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowLeft" }));

      expect(onStepChange).not.toHaveBeenCalled();
      expect(onExit).not.toHaveBeenCalled();
      expect(handle.currentIndex()).toBe(0);
      expect(document.querySelector(".driver-popover")).not.toBeNull();

      handle.dispose();
    });

    it("reports the exit once on Escape", () => {
      const onExit = vi.fn();
      startTour(TWO_STEPS, LABELS, { onExit });

      window.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape" }));

      expect(onExit).toHaveBeenCalledTimes(1);
      expect(document.querySelector(".driver-popover")).toBeNull();
    });

    it("stops handling arrow keys after the caller disposes the tour", () => {
      const onStepChange = vi.fn();
      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn(), onStepChange });

      handle.dispose();
      window.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowRight" }));

      expect(onStepChange).not.toHaveBeenCalled();
    });
  });

  describe("peripheral isolation", () => {
    // driver.js blocks pointer-events/Tab only; virtual screen-reader cursors can still read underlying UIs.
    // Mark existing body children (#app-root plus Dialog/Sheet Portals mounted under body,
    // siblings of #app-root) inert to remove them from accessibility trees,
    // making entire tours truly read-only.
    function withAppRoot(): HTMLElement {
      const appRoot = document.createElement("div");
      appRoot.id = "app-root";
      document.body.appendChild(appRoot);
      return appRoot;
    }

    it("marks #app-root inert while the tour is active, and clears it on close", () => {
      const appRoot = withAppRoot();
      const onExit = vi.fn();
      startTour(TWO_STEPS, LABELS, { onExit });

      expect(appRoot.inert).toBe(true);

      click(".driver-popover-close-btn");

      expect(appRoot.inert).toBe(false);
      appRoot.remove();
    });

    it("clears #app-root inert when the caller disposes the tour", () => {
      const appRoot = withAppRoot();
      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      expect(appRoot.inert).toBe(true);

      handle.dispose();

      expect(appRoot.inert).toBe(false);
      appRoot.remove();
    });

    it("restores a peripheral element's pre-existing inert state instead of forcing it false", () => {
      const appRoot = withAppRoot();
      const preInerted = document.createElement("div");
      preInerted.inert = true;
      document.body.appendChild(preInerted);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });
      expect(preInerted.inert).toBe(true);

      handle.dispose();

      expect(preInerted.inert).toBe(true);
      appRoot.remove();
      preInerted.remove();
    });

    it("also inerts a dialog already portaled to body when the tour starts, and clears it on dispose", () => {
      const appRoot = withAppRoot();
      // Simulate body-mounted Portal dialogs,
      // siblings of #app-root rather than descendants.
      const modal = document.createElement("div");
      document.body.appendChild(modal);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      expect(modal.inert).toBe(true);

      handle.dispose();

      expect(modal.inert).toBe(false);
      appRoot.remove();
      modal.remove();
    });

    it("suppresses a peripheral document keydown listener while the tour is active, and restores it on dispose", () => {
      const appRoot = withAppRoot();
      const onKeyDown = vi.fn();
      document.addEventListener("keydown", onKeyDown);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      expect(onKeyDown).not.toHaveBeenCalled();

      handle.dispose();

      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      expect(onKeyDown).toHaveBeenCalledTimes(1);

      document.removeEventListener("keydown", onKeyDown);
      appRoot.remove();
    });

    it("re-locks the interactive step's ancestor chain (not just its siblings) once the step is left", () => {
      const appRoot = withAppRoot();
      const target = document.createElement("button");
      target.setAttribute("data-onboarding", ONBOARDING_ANCHORS.lobbyDemoCard);
      appRoot.appendChild(target);

      const steps: TourStep[] = [
        { anchor: ONBOARDING_ANCHORS.lobbyDemoCard, title: "演示卡", body: "点这里", interactive: true },
        { anchor: null, title: "收尾", body: "结束" },
      ];
      const handle = startTour(steps, LABELS, { onExit: vi.fn() });

      // interactive steps remove inert throughout target ancestor chains, including #app-root, to enable access.
      expect(appRoot.inert).toBe(false);

      click(".driver-popover-next-btn");

      // Leaving must relock chains; restoring only hole siblings is insufficient, also restore chain nodes themselves
      // or #app-root remains accessible throughout subsequent steps.
      expect(appRoot.inert).toBe(true);

      handle.dispose();
      target.remove();
      appRoot.remove();
    });

    it("does not suppress Tab so driver's own focus trap keeps working", () => {
      const appRoot = withAppRoot();
      const onKeyDown = vi.fn();
      document.addEventListener("keydown", onKeyDown);

      const handle = startTour(TWO_STEPS, LABELS, { onExit: vi.fn() });

      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
      expect(onKeyDown).toHaveBeenCalledTimes(1);

      handle.dispose();
      document.removeEventListener("keydown", onKeyDown);
      appRoot.remove();
    });
  });
});
