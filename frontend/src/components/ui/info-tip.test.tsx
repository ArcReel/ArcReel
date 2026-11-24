import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InfoTip, InfoTipContent, InfoTipTrigger } from "./info-tip";

const HINT = "不含缓存命中与缓存写入的部分";

function InputHint() {
  return (
    <InfoTip>
      <InfoTipTrigger render={<button type="button" className="underline decoration-dotted" />}>输入</InfoTipTrigger>
      <InfoTipContent title="输入">{HINT}</InfoTipContent>
    </InfoTip>
  );
}

const trigger = () => screen.getByRole("button", { name: "输入" });

describe("InfoTip", () => {
  it("renders a native button, styled through render", () => {
    render(<InputHint />);

    expect(trigger().tagName).toBe("BUTTON");
    expect(trigger()).toHaveAttribute("type", "button");
    expect(trigger()).toHaveClass("decoration-dotted", "focus-ring");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens on hover", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.hover(trigger());

    expect(await screen.findByRole("dialog")).toHaveTextContent(HINT);
  });

  it.each([0, 1_000])("keeps a hover-opened hint visible on the first click after %d ms", async (elapsed) => {
    vi.useFakeTimers();
    try {
      render(<InputHint />);
      const button = trigger();

      fireEvent.pointerOver(button, { pointerType: "mouse" });
      fireEvent.mouseEnter(button);
      expect(screen.getByRole("dialog")).toHaveTextContent(HINT);
      fireEvent.pointerDown(button, { pointerType: "mouse" });
      await act(() => vi.advanceTimersByTimeAsync(elapsed));
      fireEvent.click(button, { detail: 1 });

      expect(button).toHaveAttribute("aria-expanded", "true");
      fireEvent.click(button, { detail: 1 });
      expect(button).toHaveAttribute("aria-expanded", "false");
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens and closes on touch taps", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.pointer({ keys: "[TouchA]", target: trigger() });

    expect(await screen.findByRole("dialog")).toHaveTextContent(HINT);
    await user.pointer({ keys: "[TouchA]", target: trigger() });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it.each(["{Enter}", " "])("opens from the keyboard with %s", async (key) => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.tab();
    expect(trigger()).toHaveFocus();
    await user.keyboard(key);

    expect(await screen.findByRole("dialog")).toHaveTextContent(HINT);
  });

  it("closes on Escape and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.tab();
    await user.keyboard("{Enter}");
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger()).toHaveFocus();
  });

  it("names the popup after the trigger text", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.click(trigger());

    expect(await screen.findByRole("dialog", { name: "输入" })).toBeInTheDocument();
  });

  it("exposes the hint as the popup's accessible description", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.click(trigger());

    expect(await screen.findByRole("dialog")).toHaveAccessibleDescription(HINT);
    expect(trigger()).toHaveAttribute("aria-expanded", "true");
  });
});
