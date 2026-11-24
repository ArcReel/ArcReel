import { useState } from "react";
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

  it("opens on hover and closes when the pointer leaves", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.hover(trigger());

    expect(await screen.findByRole("dialog")).toHaveTextContent(HINT);
    await user.unhover(trigger());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it.each([0, 1_000])(
    "pins a hover-opened hint across pointer leave on the first click after %d ms and closes on the second click",
    async (elapsed) => {
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
        fireEvent.mouseLeave(button);
        expect(button).toHaveAttribute("aria-expanded", "true");
        expect(screen.getByRole("dialog")).toHaveTextContent(HINT);

        fireEvent.click(button, { detail: 1 });
        expect(button).toHaveAttribute("aria-expanded", "false");
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("closes a hover-pinned hint on Escape after pointer leave", async () => {
    vi.useFakeTimers();
    try {
      render(<InputHint />);
      const button = trigger();

      fireEvent.pointerOver(button, { pointerType: "mouse" });
      fireEvent.mouseEnter(button);
      fireEvent.pointerDown(button, { pointerType: "mouse" });
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      fireEvent.click(button, { detail: 1 });
      fireEvent.mouseLeave(button);
      expect(button).toHaveAttribute("aria-expanded", "true");

      fireEvent.keyDown(document, { key: "Escape" });
      expect(button).toHaveAttribute("aria-expanded", "false");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps controlled open state in sync across hover pinning and programmatic resets", async () => {
    vi.useFakeTimers();
    try {
      const onOpenChange = vi.fn();
      function ControlledHint() {
        const [open, setOpen] = useState(false);
        return (
          <>
            <InfoTip
              open={open}
              onOpenChange={(nextOpen, details) => {
                onOpenChange(nextOpen, details.reason);
                setOpen(nextOpen);
              }}
            >
              <InfoTipTrigger render={<button type="button" />}>输入</InfoTipTrigger>
              <InfoTipContent title="输入">{HINT}</InfoTipContent>
            </InfoTip>
            <button type="button" onClick={() => setOpen(false)}>
              关闭
            </button>
            <button type="button" onClick={() => setOpen(true)}>
              打开
            </button>
          </>
        );
      }

      render(<ControlledHint />);
      const button = trigger();

      fireEvent.pointerOver(button, { pointerType: "mouse" });
      fireEvent.mouseEnter(button);
      expect(button).toHaveAttribute("aria-expanded", "true");

      fireEvent.pointerDown(button, { pointerType: "mouse" });
      await act(() => vi.advanceTimersByTimeAsync(1_000));
      fireEvent.click(button, { detail: 1 });
      fireEvent.mouseLeave(button);
      expect(button).toHaveAttribute("aria-expanded", "true");
      expect(onOpenChange).toHaveBeenCalledTimes(1);
      expect(onOpenChange).toHaveBeenLastCalledWith(true, "trigger-hover");

      fireEvent.click(button, { detail: 1 });
      expect(button).toHaveAttribute("aria-expanded", "false");
      expect(onOpenChange).toHaveBeenLastCalledWith(false, "trigger-press");

      fireEvent.pointerOver(button, { pointerType: "mouse" });
      fireEvent.mouseEnter(button);
      expect(button).toHaveAttribute("aria-expanded", "true");

      fireEvent.click(screen.getByRole("button", { name: "关闭" }));
      expect(button).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(screen.getByRole("button", { name: "打开" }));
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
