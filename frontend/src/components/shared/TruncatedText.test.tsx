import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TruncatedText } from "./TruncatedText";

const PATH = "episodes/episode_12/storyboards/scene_07_shot_03_final_render.png";

// jsdom 不排版：用内容宽度与可见宽度模拟截断。
function mockWidths(scrollWidth: number, clientWidth: number) {
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", { configurable: true, get: () => scrollWidth });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => clientWidth });
}

// 触屏档：没有悬停能力，点按才显示全文。
function stubNoHover() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === "(hover: none)",
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => {
  mockWidths(0, 0);
  vi.unstubAllGlobals();
});

function renderText(props: { revealOnTap?: boolean } = {}) {
  render(
    <TooltipProvider>
      <button type="button">before</button>
      <TruncatedText text={PATH} {...props} />
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

  it("reveals the full text on tap when there is no hover", async () => {
    mockWidths(480, 200);
    stubNoHover();
    renderText({ revealOnTap: true });

    fireEvent.click(screen.getByText(PATH));
    await waitFor(() => expect(shownTimes()).toBe(2));
  });

  it("keeps the hover tooltip on pointer devices even with revealOnTap", async () => {
    mockWidths(480, 200);
    renderText({ revealOnTap: true });

    expect(screen.getByText(PATH)).toHaveAttribute("data-slot", "tooltip-trigger");
    fireEvent.mouseEnter(screen.getByText(PATH));
    await waitFor(() => expect(shownTimes()).toBe(2));
  });
});
