import { render } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { useAutoFocus } from "./useAutoFocus";

describe("useAutoFocus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls focus on the rendered element when enabled (default)", () => {
    const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");

    function TestComponent() {
      const ref = useAutoFocus<HTMLInputElement>();
      return <input ref={ref} data-testid="input" />;
    }
    render(<TestComponent />);
    expect(focusSpy).toHaveBeenCalled();
  });

  it("does not call focus when enabled=false", () => {
    const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");

    function TestComponent() {
      const ref = useAutoFocus<HTMLInputElement>(false);
      return <input ref={ref} data-testid="input" />;
    }
    render(<TestComponent />);
    expect(focusSpy).not.toHaveBeenCalled();
  });
});
