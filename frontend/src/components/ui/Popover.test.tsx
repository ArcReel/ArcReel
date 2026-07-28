import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Popover } from "./Popover";

class MockResizeObserver {
  observe() {}
  disconnect() {}
  unobserve() {}
}

function RefHarness({ onClose }: { onClose?: () => void }) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  return (
    <div>
      <button ref={anchorRef} data-testid="anchor" type="button">
        anchor
      </button>
      <Popover open anchorRef={anchorRef} onClose={onClose}>
        <div data-testid="panel-content">hello</div>
      </Popover>
    </div>
  );
}

/**
 * Parent-as-anchor pattern (GlobalHeader / ProjectsPage 等业务实际用法):
 * ref 挂在 Popover 的父节点上，而不是兄弟节点。
 * 此模式下 Popover 作为子 fiber 的 layout effect 在父 fiber 的 ref attach 之前执行,
 * 如果 effect 只跑一次，会读到 null 并导致 floating-ui 无 reference、定位到左上角。
 */
function ParentRefHarness() {
  const anchorRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <div ref={anchorRef} data-testid="parent-anchor">
      <button type="button" onClick={() => setOpen(true)}>
        open
      </button>
      <Popover open={open} anchorRef={anchorRef} onClose={() => setOpen(false)}>
        <div data-testid="panel-content">hello</div>
      </Popover>
    </div>
  );
}

function ElementHarness({ onClose }: { onClose?: () => void }) {
  const [el, setEl] = useState<HTMLButtonElement | null>(null);
  return (
    <div>
      <button ref={setEl} data-testid="anchor" type="button">
        anchor
      </button>
      <Popover open anchorElement={el} onClose={onClose}>
        <div data-testid="panel-content">hello</div>
      </Popover>
    </div>
  );
}

function MaxHeightHarness() {
  const anchorRef = useRef<HTMLButtonElement>(null);
  return (
    <div>
      <button ref={anchorRef} data-testid="anchor" type="button">
        anchor
      </button>
      <Popover open anchorRef={anchorRef} maxHeight={288}>
        <div data-testid="panel-content" style={{ height: "600px" }}>
          big content
        </div>
      </Popover>
    </div>
  );
}

describe("Popover", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function ClosedHarness() {
    const anchorRef = useRef<HTMLButtonElement>(null);
    return (
      <div>
        <button ref={anchorRef} type="button">
          anchor
        </button>
        <Popover open={false} anchorRef={anchorRef}>
          <div data-testid="panel-content">hidden</div>
        </Popover>
      </div>
    );
  }

  it("renders nothing when open=false", () => {
    render(<ClosedHarness />);
    expect(screen.queryByTestId("panel-content")).toBeNull();
  });

  it("portals the panel under document.body (not inside the render container)", () => {
    const { container } = render(<RefHarness />);
    const panel = screen.getByTestId("panel-content");
    expect(container.contains(panel)).toBe(false);
    expect(document.body.contains(panel)).toBe(true);
  });

  it("invokes onClose when Escape is pressed", () => {
    const onClose = vi.fn();
    render(<RefHarness onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("invokes onClose on outside pointerdown", () => {
    const onClose = vi.fn();
    render(
      <div>
        <button data-testid="outside" type="button">
          outside
        </button>
        <RefHarness onClose={onClose} />
      </div>,
    );
    fireEvent.pointerDown(screen.getByTestId("outside"));
    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(onClose).toHaveBeenCalled();
  });

  it("does not close on pointerdown inside the panel", () => {
    const onClose = vi.fn();
    render(<RefHarness onClose={onClose} />);
    fireEvent.pointerDown(screen.getByTestId("panel-content"));
    fireEvent.mouseDown(screen.getByTestId("panel-content"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not close on pointerdown on the anchor (reference element is excluded)", () => {
    const onClose = vi.fn();
    render(<ElementHarness onClose={onClose} />);
    fireEvent.pointerDown(screen.getByTestId("anchor"));
    fireEvent.mouseDown(screen.getByTestId("anchor"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("applies floating-ui positioning styles to the panel root", () => {
    render(<RefHarness />);
    const panel = screen.getByTestId("panel-content").parentElement!;
    expect(panel.style.position).toBe("fixed");
    // floating-ui writes top/left to 0 and uses transform for position
    expect(panel.style.top).toBe("0px");
    expect(panel.style.left).toBe("0px");
  });

  it("binds reference when opening a Popover whose anchorRef is on a parent node (regression: left-top anchoring)", async () => {
    // 业务里最常见的用法——ref 挂在父节点上，open 从 false 切到 true。
    // 如果 Popover 的 layout effect 只跑一次，首次执行时父节点 ref 还没 attach
    // (React commit phase post-order)，setReference(null) 就被锁死，
    // floating-ui 没有 reference，面板停在左上角 (transform 为空)。
    //
    // jsdom 默认 getBoundingClientRect 全 0，绑定与否 transform 都是 translate(0,0)。
    // 给 anchor 节点注入一个非零 rect，差异才能显现：绑定成功时 transform
    // 按非零锚点计算；未绑定时 floating-ui 默认 translate(0,0)。
    vi.spyOn(HTMLDivElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 300,
      y: 400,
      width: 80,
      height: 40,
      top: 400,
      left: 300,
      right: 380,
      bottom: 440,
      toJSON() {
        return this;
      },
    } as DOMRect);

    const user = userEvent.setup();
    render(<ParentRefHarness />);
    await user.click(screen.getByText("open"));
    const panel = screen.getByTestId("panel-content").parentElement!;
    // 绑定成功时 floating-ui 至少会输出非零 translate。
    expect(panel.style.transform).not.toBe("translate(0px, 0px)");
    expect(panel.style.transform).not.toBe("");
  });

  it("accepts maxHeight prop without throwing (size middleware opt-in)", () => {
    // JSDOM 缺乏 viewport measurement，floating-ui 的 size 中间件 apply 回调
    // 不一定被调用；此处只确认传入 maxHeight 时 Popover 仍正常挂载且 portal 成功。
    render(<MaxHeightHarness />);
    expect(screen.getByTestId("panel-content")).toBeInTheDocument();
  });
});
