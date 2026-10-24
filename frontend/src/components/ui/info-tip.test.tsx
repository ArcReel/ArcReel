import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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

  it("opens on click, for touch users", async () => {
    const user = userEvent.setup();
    render(<InputHint />);

    await user.click(trigger());

    expect(await screen.findByRole("dialog")).toHaveTextContent(HINT);
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

    await user.click(trigger());
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
