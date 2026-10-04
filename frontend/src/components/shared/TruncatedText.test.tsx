import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TruncatedText } from "./TruncatedText";

const PATH = "episodes/episode_12/storyboards/scene_07_shot_03_final_render.png";

// jsdom 不排版：用内容宽度与可见宽度模拟截断。
function mockWidths(scrollWidth: number, clientWidth: number) {
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", { configurable: true, get: () => scrollWidth });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => clientWidth });
}

afterEach(() => {
  mockWidths(0, 0);
});

function renderText() {
  render(
    <TooltipProvider>
      <button type="button">before</button>
      <TruncatedText text={PATH} />
    </TooltipProvider>,
  );
}

// 全文提示出现时，页面上同时有截断的原文与弹出的全文两份文字。
const shownTimes = () => screen.queryAllByText(PATH).length;

describe("TruncatedText", () => {
  it("lets keyboard users reach truncated text and shows the full text on focus", async () => {
    mockWidths(480, 200);
    const user = userEvent.setup();
    renderText();

    await user.tab();
    await user.tab();
    expect(document.activeElement).toHaveTextContent(PATH);
    await waitFor(() => expect(shownTimes()).toBe(2));
  });

  it("shows the full text on hover when truncated", async () => {
    mockWidths(480, 200);
    renderText();

    fireEvent.mouseEnter(screen.getByText(PATH));
    await waitFor(() => expect(shownTimes()).toBe(2));
  });

  it("stays out of the tab order and shows no full-text popup when the text fits", async () => {
    mockWidths(200, 200);
    const user = userEvent.setup();
    renderText();

    await user.tab();
    await user.tab();
    expect(screen.getByText(PATH)).not.toHaveFocus();

    await user.hover(screen.getByText(PATH));
    expect(shownTimes()).toBe(1);
  });
});
