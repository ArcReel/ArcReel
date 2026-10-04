import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { Turn } from "@/types";
import { UI_LAYERS } from "@/utils/ui-layers";
import { AgentCopilot } from "./AgentCopilot";

vi.mock("@/hooks/useAssistantSession", () => ({
  useAssistantSession: vi.fn(),
}));

vi.mock("./ContextBanner", () => ({
  ContextBanner: () => <div data-testid="context-banner" />,
}));

vi.mock("./SlashCommandMenu", () => ({
  SlashCommandMenu: vi.fn(() => null),
}));

const mockedUseAssistantSession = vi.mocked(useAssistantSession);

// jsdom 没有布局：消息区的视口按 400px 高、每条消息按 300px 高打桩，滚动位置按元素记住，
// scrollTo 直接写入并派发 scroll 事件。
const VIEWPORT_HEIGHT = 400;
const ITEM_HEIGHT = 300;

function stubMessageLayout() {
  const scrollTops = new WeakMap<Element, number>();
  const isViewport = (el: Element) => el instanceof HTMLElement && el.dataset.slot === "message-scroller-viewport";
  const itemsOf = (viewport: Element) =>
    Array.from(viewport.querySelectorAll<HTMLElement>('[data-slot="message-scroller-item"]'));

  vi.spyOn(Element.prototype, "scrollTop", "get").mockImplementation(function (this: Element) {
    return scrollTops.get(this) ?? 0;
  });
  vi.spyOn(Element.prototype, "scrollTop", "set").mockImplementation(function (this: Element, value: number) {
    scrollTops.set(this, value);
  });
  vi.spyOn(Element.prototype, "clientHeight", "get").mockImplementation(function (this: Element) {
    return isViewport(this) ? VIEWPORT_HEIGHT : 0;
  });
  vi.spyOn(Element.prototype, "scrollHeight", "get").mockImplementation(function (this: Element) {
    return isViewport(this) ? itemsOf(this).length * ITEM_HEIGHT : 0;
  });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (isViewport(this)) return new DOMRect(0, 0, 420, VIEWPORT_HEIGHT);
    const viewport = this.closest('[data-slot="message-scroller-viewport"]');
    if (!viewport || !(this instanceof HTMLElement) || this.dataset.slot !== "message-scroller-item") {
      return new DOMRect();
    }
    const index = itemsOf(viewport).indexOf(this);
    return new DOMRect(0, index * ITEM_HEIGHT - viewport.scrollTop, 420, ITEM_HEIGHT);
  });
  Element.prototype.scrollTo = function (this: Element, options?: ScrollToOptions | number) {
    if (typeof options === "object" && options.top !== undefined) this.scrollTop = options.top;
    this.dispatchEvent(new Event("scroll"));
  } as Element["scrollTo"];
}

function makeTurns(count: number): Turn[] {
  return Array.from({ length: count }, (_, index) => ({
    type: index % 2 === 0 ? "user" : "assistant",
    uuid: `turn-${index}`,
    content: [{ type: "text", text: `第 ${index + 1} 条消息` }],
  }));
}

function makePendingQuestion() {
  return {
    question_id: "q-1",
    questions: [
      {
        header: "输出",
        question: "输出格式是什么？",
        multiSelect: false,
        options: [
          { label: "摘要", description: "简洁输出" },
          { label: "详细", description: "完整说明" },
        ],
      },
    ],
  };
}

describe("AgentCopilot", () => {
  // Mocks whose callers wrap them with voidPromise must return a Promise
  // so the .catch(...) chain in voidPromise resolves instead of crashing.
  const sendMessage = vi.fn().mockResolvedValue(undefined);
  const rewriteMessage = vi.fn().mockResolvedValue(true);
  const answerQuestion = vi.fn().mockResolvedValue(undefined);
  const interrupt = vi.fn().mockResolvedValue(undefined);
  const createNewSession = vi.fn();
  const switchSession = vi.fn().mockResolvedValue(undefined);
  const deleteSession = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    useAssistantStore.setState(useAssistantStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.clearAllMocks();

    useProjectsStore.getState().setCurrentProject("demo", null);
    mockedUseAssistantSession.mockReturnValue({
      sendMessage,
      rewriteMessage,
      answerQuestion,
      interrupt,
      createNewSession,
      switchSession,
      deleteSession,
    });
  });

  it("renders the pending-question wizard and disables normal sending", () => {
    useAssistantStore.setState({
      pendingQuestion: makePendingQuestion(),
      skills: [{ name: "plan", description: "Plan", scope: "project", path: "/tmp/plan" }],
    });

    render(<AgentCopilot />);

    expect(screen.getByText("需要你的选择")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent 输入")).toBeDisabled();
    expect(screen.getByLabelText("发送消息")).toBeDisabled();
    expect(screen.getByPlaceholderText("请先回答上方问题")).toBeInTheDocument();
  });

  it("submits wizard answers through answerQuestion", () => {
    useAssistantStore.setState({
      pendingQuestion: makePendingQuestion(),
    });

    render(<AgentCopilot />);

    fireEvent.click(screen.getByLabelText("摘要"));
    fireEvent.click(screen.getByRole("button", { name: /完成并提交/ }));

    expect(answerQuestion).toHaveBeenCalledWith("q-1", {
      "输出格式是什么？": "摘要",
    });
  });

  it("keeps assistant root isolated and uses local popover layer for session history", () => {
    useAssistantStore.setState({
      sessions: [
        {
          id: "session-1",
          project_name: "demo",
          title: "当前会话",
          status: "idle",
          created_at: "2026-02-01T00:00:00Z",
          updated_at: "2026-02-01T00:00:00Z",
        },
      ],
      currentSessionId: "session-1",
    });

    const { container } = render(<AgentCopilot />);

    expect(container.firstElementChild).toHaveClass("isolate");

    fireEvent.click(screen.getByTitle("切换会话"));
    expect(document.querySelector(`.${UI_LAYERS.assistantLocalPopover}`)).toBeTruthy();
  });

  it("does not send when Enter is used to confirm an IME composition", () => {
    render(<AgentCopilot />);

    const textarea = screen.getByLabelText("Agent 输入");
    fireEvent.change(textarea, { target: { value: "你好" } });

    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, {
      key: "Enter",
      code: "Enter",
      keyCode: 229,
      which: 229,
      isComposing: true,
    });

    expect(sendMessage).not.toHaveBeenCalled();

    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, {
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13,
    });

    expect(sendMessage).toHaveBeenCalledWith("你好", undefined);
  });

  it("consumes a one-shot prefill dispatched via the assistant store's input field", async () => {
    render(<AgentCopilot />);

    act(() => {
      useAssistantStore.getState().setInput("为第 1 集生成剧本");
    });

    expect(screen.getByLabelText("Agent 输入")).toHaveValue("为第 1 集生成剧本");

    await waitFor(() => {
      expect(useAssistantStore.getState().input).toBe("");
    });
  });

  describe("message flow scrolling", () => {
    const scrollTo = Element.prototype.scrollTo;

    beforeEach(() => {
      stubMessageLayout();
    });

    afterEach(() => {
      vi.restoreAllMocks();
      Element.prototype.scrollTo = scrollTo;
    });

    it("offers jump-to-latest once the user scrolls up, and returns to the end on send", async () => {
      useAssistantStore.setState({ currentSessionId: "session-1", turns: makeTurns(6) });
      render(<AgentCopilot />);

      const viewport = screen.getByRole("region", { name: "对话记录" });
      const jump = screen.getByRole("button", { name: "跳到最新" });
      const end = 6 * ITEM_HEIGHT - VIEWPORT_HEIGHT;
      await waitFor(() => expect(viewport.scrollTop).toBe(end));
      expect(jump).toHaveAttribute("data-active", "false");

      // 上翻：滚轮表明是用户在滚动，跟随随之停止
      fireEvent.wheel(viewport, { deltaY: -600 });
      viewport.scrollTop = 0;
      fireEvent.scroll(viewport);
      await waitFor(() => expect(jump).toHaveAttribute("data-active", "true"));

      const input = screen.getByLabelText("Agent 输入");
      fireEvent.change(input, { target: { value: "继续" } });
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

      expect(sendMessage).toHaveBeenCalledWith("继续", undefined);
      expect(viewport.scrollTop).toBe(end);
      await waitFor(() => expect(jump).toHaveAttribute("data-active", "false"));
    });
  });
});
