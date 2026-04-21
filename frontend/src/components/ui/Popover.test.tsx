import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

  it("accepts maxHeight prop without throwing (size middleware opt-in)", () => {
    // JSDOM 缺乏 viewport measurement，floating-ui 的 size 中间件 apply 回调
    // 不一定被调用；此处只确认传入 maxHeight 时 Popover 仍正常挂载且 portal 成功。
    render(<MaxHeightHarness />);
    expect(screen.getByTestId("panel-content")).toBeInTheDocument();
  });
});
