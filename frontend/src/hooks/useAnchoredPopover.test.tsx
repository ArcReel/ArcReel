import { createPortal } from "react-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";
import { useAnchoredPopover } from "./useAnchoredPopover";

class MockResizeObserver {
  observe() {}
  disconnect() {}
  unobserve() {}
}

function PopoverHarness({ onClose }: { onClose: () => void }) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const { panelRef, positionStyle } = useAnchoredPopover({
    open: true,
    anchorRef,
    onClose,
    sideOffset: 8,
  });

  return (
    <div>
      <button ref={anchorRef} data-testid="anchor" type="button">
        anchor
      </button>
      {createPortal(
        <div ref={panelRef} data-testid="popover" style={positionStyle}>
          popover
        </div>,
        document.body,
      )}
    </div>
  );
}

describe("useAnchoredPopover", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
    Object.defineProperty(document.documentElement, "clientWidth", {
      configurable: true,
      value: 1200,
    });

    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const testId = this.getAttribute("data-testid");

      if (testId === "anchor") {
        return DOMRect.fromRect({
          x: 500,
          y: 60,
          width: 80,
          height: 32,
        });
      }

      if (testId === "popover") {
        return DOMRect.fromRect({
          x: 0,
          y: 0,
          width: 320,
          height: 240,
        });
      }

      return DOMRect.fromRect();
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("positions the popover from anchor and panel measurements", () => {
    render(<PopoverHarness onClose={vi.fn()} />);

    const popover = screen.getByTestId("popover");
    expect(popover.style.top).toBe("100px");
    expect(popover.style.left).toBe("260px");
  });

  it("ignores anchor and panel clicks, and closes on outside click or Escape", () => {
    const onClose = vi.fn();

    render(<PopoverHarness onClose={onClose} />);

    fireEvent.pointerDown(screen.getByTestId("anchor"));
    fireEvent.pointerDown(screen.getByTestId("popover"));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.pointerDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
