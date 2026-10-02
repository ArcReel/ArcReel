import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { createPortal } from "react-dom";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

function Trap({ name, active = true }: { name: string; active?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, active);
  return createPortal(
    <div ref={ref}>
      <button type="button">{`${name} 1`}</button>
      <button type="button">{`${name} 2`}</button>
    </div>,
    document.body,
  );
}

function Stacked({ topOpen }: { topOpen: boolean }) {
  return (
    <>
      <Trap name="底层" />
      {topOpen ? <Trap name="上层" /> : null}
    </>
  );
}

describe("useFocusTrap", () => {
  it("cycles Tab and Shift+Tab within the container", async () => {
    const user = userEvent.setup();
    render(<Trap name="底层" />);

    expect(screen.getByRole("button", { name: "底层 1" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "底层 2" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "底层 1" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "底层 2" })).toHaveFocus();
  });

  it("lands initial focus in the same commit that inserts the container into the DOM", async () => {
    // 脱离 act：模拟真实运行时 DOM 变化先于 passive effect 被观察到的情形。
    const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const previous = env.IS_REACT_ACT_ENVIRONMENT;
    env.IS_REACT_ACT_ENVIRONMENT = false;
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      const focused = new Promise<Element | null>((resolve) => {
        const observer = new MutationObserver(() => {
          if (!document.body.textContent?.includes("底层 1")) return;
          observer.disconnect();
          resolve(document.activeElement);
        });
        observer.observe(document.body, { childList: true, subtree: true });
      });
      root.render(<Trap name="底层" />);

      expect((await focused)?.textContent).toBe("底层 1");
    } finally {
      root.unmount();
      host.remove();
      env.IS_REACT_ACT_ENVIRONMENT = previous;
    }
  });

  it("lets only the most recently activated trap handle Tab, and hands Tab back when it closes", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Stacked topOpen={false} />);
    rerender(<Stacked topOpen />);

    expect(screen.getByRole("button", { name: "上层 1" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "上层 2" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "上层 1" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "上层 2" })).toHaveFocus();

    rerender(<Stacked topOpen={false} />);
    expect(screen.getByRole("button", { name: "底层 1" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "底层 2" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "底层 1" })).toHaveFocus();
  });
});
