import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { createPortal } from "react-dom";
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
