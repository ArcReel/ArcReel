import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { PendingQuestion, SessionMeta, Turn } from "@/types";
import { AgentCopilot } from "./AgentCopilot";

vi.mock("@/hooks/useAssistantSession", () => ({
  useAssistantSession: vi.fn(),
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

function makePendingQuestion(): PendingQuestion {
  return {
    question_id: "q-1",
    questions: [
      {
        header: "镜头",
        question: "结尾用哪张图作首帧？",
        multiSelect: false,
        options: [
          { label: "雨夜远景", description: "沿用第 3 镜" },
          { label: "人物特写", description: "" },
          { label: "其他", description: "" },
        ],
      },
      {
        header: "审片",
        question: "审片时重点看哪些方面？",
        multiSelect: true,
        options: [
          { label: "光线", description: "" },
          { label: "人物一致性", description: "" },
          { label: "节奏", description: "" },
        ],
      },
    ],
  };
}

function makeSession(id: string, title: string, overrides: Partial<SessionMeta> = {}): SessionMeta {
  return {
    id,
    project_name: "demo",
    title,
    status: "idle",
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
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
  const deleteSession = vi.fn().mockResolvedValue(true);
  const withdrawQueuedMessage = vi.fn().mockResolvedValue(undefined);
  const resendQueuedMessage = vi.fn().mockResolvedValue(undefined);
  const sendQueuedMessageNow = vi.fn().mockResolvedValue(undefined);

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
      withdrawQueuedMessage,
      resendQueuedMessage,
      sendQueuedMessageNow,
    });
  });

  describe("questions", () => {
    it("takes the composer's place, answers a single and a multi choice by number keys, then submits", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({ currentSessionId: "session-1", sessionStatus: "running", pendingQuestion: makePendingQuestion() });
      render(<AgentCopilot />);

      expect(screen.queryByRole("combobox", { name: "Agent 输入" })).not.toBeInTheDocument();
      const form = screen.getByRole("form", { name: "Agent 的提问" });
      expect(within(form).getByRole("progressbar")).toHaveTextContent("第 1 / 2 题");

      // 没选就前进：留在原题并提示
      await user.click(within(form).getByRole("button", { name: "下一题" }));
      expect(within(form).getByRole("progressbar")).toHaveTextContent("第 1 / 2 题");
      expect(within(form).getByRole("alert")).toHaveTextContent("选择一项，或写下你的回答");

      await user.keyboard("2");
      expect(within(form).getByRole("radio", { name: /人物特写/ })).toBeChecked();
      await user.click(within(form).getByRole("button", { name: "下一题" }));
      expect(within(form).getByRole("progressbar")).toHaveTextContent("第 2 / 2 题");

      await user.keyboard("1");
      await user.keyboard("3");
      await user.click(within(form).getByRole("button", { name: "提交回答" }));

      expect(answerQuestion).toHaveBeenCalledWith("q-1", {
        "结尾用哪张图作首帧？": "人物特写",
        "审片时重点看哪些方面？": "光线, 节奏",
      });
    });

    it("sends a free-form answer in place of the agent's own 「其他」 option", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({
        currentSessionId: "session-1",
        pendingQuestion: { question_id: "q-2", questions: [makePendingQuestion().questions[0]] },
      });
      render(<AgentCopilot />);

      const form = screen.getByRole("form", { name: "Agent 的提问" });
      expect(within(form).queryByRole("radio", { name: /其他/ })).not.toBeInTheDocument();
      await user.click(within(form).getByRole("radio", { name: /雨夜远景/ }));
      await user.type(within(form).getByRole("textbox", { name: "其他回答" }), "用上一版的城门");
      expect(within(form).getByRole("radio", { name: /雨夜远景/ })).not.toBeChecked();
      await user.click(within(form).getByRole("button", { name: "提交回答" }));

      expect(answerQuestion).toHaveBeenCalledWith("q-2", { "结尾用哪张图作首帧？": "用上一版的城门" });
    });

    it("leaves a question → answer receipt on the user side once the answer is recorded", () => {
      useAssistantStore.setState({
        currentSessionId: "session-1",
        turns: [
          {
            type: "user",
            uuid: "qa-1",
            content: [{ type: "question_answer", answers: { "结尾用哪张图作首帧？": "人物特写" } }],
          },
        ],
      });
      render(<AgentCopilot />);

      const receipt = screen.getByRole("term");
      expect(receipt).toHaveTextContent("结尾用哪张图作首帧？");
      expect(screen.getByRole("definition")).toHaveTextContent("人物特写");
    });
  });

  describe("session history", () => {
    beforeEach(() => {
      useAssistantStore.setState({
        sessions: [
          makeSession("session-1", "第 1 集开场节奏"),
          makeSession("session-2", "导入原著第 6–9 章", { status: "error" }),
          makeSession("session-3", "给朱汉杨补服装衍生"),
        ],
        currentSessionId: "session-1",
      });
    });

    it("shows the current session title, searches the list and switches sessions", async () => {
      const user = userEvent.setup();
      render(<AgentCopilot />);

      expect(screen.getByRole("heading", { name: "第 1 集开场节奏" })).toBeInTheDocument();
      const toggle = screen.getByRole("button", { name: "会话历史" });
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-pressed", "true");
      // 历史视图打开时输入框仍可用
      expect(screen.getByRole("combobox", { name: "Agent 输入" })).toBeEnabled();

      await user.type(screen.getByRole("searchbox", { name: "搜索会话" }), "原著");
      expect(screen.queryByRole("button", { name: /第 1 集开场节奏/ })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: /^(?!删除).*导入原著第 6–9 章/ }));

      expect(switchSession).toHaveBeenCalledWith("session-2");
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByRole("searchbox", { name: "搜索会话" })).not.toBeInTheDocument();
    });

    it("says so when nothing matches the search", async () => {
      const user = userEvent.setup();
      render(<AgentCopilot />);
      await user.click(screen.getByRole("button", { name: "会话历史" }));
      await user.type(screen.getByRole("searchbox", { name: "搜索会话" }), "剪辑");
      expect(screen.getByRole("status")).toHaveTextContent("没有匹配的会话");
    });

    it.each(["queued", "unsent"] as const)("returns from history when sending a %s tray message", async (state) => {
      const user = userEvent.setup();
      useAssistantStore.setState({ queuedMessages: [{ id: "q-1", content: [{ type: "text", text: "改结局" }], state }] });
      render(<AgentCopilot />);
      const toggle = screen.getByRole("button", { name: "会话历史" });
      await user.click(toggle);
      expect(screen.getByRole("searchbox", { name: "搜索会话" })).toBeInTheDocument();
      const tray = screen.getByRole("list", { name: "排队消息" });
      await user.click(within(tray).getByRole("button", { name: state === "queued" ? "立即发送" : "发送" }));
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByRole("searchbox", { name: "搜索会话" })).not.toBeInTheDocument();
    });

    it("deletes a session only after confirming in an alert dialog", async () => {
      const user = userEvent.setup();
      deleteSession.mockImplementationOnce(async (id: string) => {
        useAssistantStore.setState((s) => ({ sessions: s.sessions.filter((item) => item.id !== id) }));
        return true;
      });
      render(<AgentCopilot />);
      await user.click(screen.getByRole("button", { name: "会话历史" }));

      await user.click(screen.getByRole("button", { name: "删除会话「给朱汉杨补服装衍生」" }));
      const dialog = await screen.findByRole("alertdialog", { name: "删除会话「给朱汉杨补服装衍生」？" });
      await waitFor(() => expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus());
      expect(deleteSession).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole("button", { name: "删除会话" }));
      expect(deleteSession).toHaveBeenCalledWith("session-3");
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(screen.queryByRole("button", { name: /给朱汉杨补服装衍生/ })).not.toBeInTheDocument();
    });

    it("closes the dialog once deleting succeeds, before the list catches up", async () => {
      const user = userEvent.setup();
      // 删除成功由返回值告知，不看列表此刻是否已经去掉这一条
      deleteSession.mockResolvedValueOnce(true);
      render(<AgentCopilot />);
      await user.click(screen.getByRole("button", { name: "会话历史" }));
      await user.click(screen.getByRole("button", { name: "删除会话「给朱汉杨补服装衍生」" }));
      const dialog = await screen.findByRole("alertdialog");
      await user.click(within(dialog).getByRole("button", { name: "删除会话" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    });

    it("keeps the dialog open with an error when deleting fails", async () => {
      const user = userEvent.setup();
      deleteSession.mockResolvedValueOnce(false);
      render(<AgentCopilot />);
      await user.click(screen.getByRole("button", { name: "会话历史" }));
      await user.click(screen.getByRole("button", { name: "删除会话「给朱汉杨补服装衍生」" }));
      const dialog = await screen.findByRole("alertdialog");
      await user.click(within(dialog).getByRole("button", { name: "删除会话" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent("会话没有删除成功，请重试。");
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    });
  });

  describe("composer", () => {
    const SKILLS = [
      { name: "video-workflow", description: "完整工作流", scope: "project" as const, path: "/tmp/a" },
      { name: "generate-storyboard", description: "为剧本分镜生成分镜图", scope: "project" as const, path: "/tmp/b" },
      { name: "generate-video", description: "生成视频片段", scope: "project" as const, path: "/tmp/c" },
    ];

    it("filters skills after 「/」 and inserts the one picked with the arrow keys", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({ skills: SKILLS });
      render(<AgentCopilot />);

      const input = screen.getByRole("combobox", { name: "Agent 输入" });
      await user.type(input, "/分镜");
      const menu = await screen.findByRole("listbox", { name: "技能命令" });
      expect(input).toHaveAttribute("aria-expanded", "true");
      expect(within(menu).getAllByRole("option")).toHaveLength(1);

      await user.clear(input);
      await user.type(input, "先 /");
      expect(within(await screen.findByRole("listbox")).getAllByRole("option")).toHaveLength(3);
      await user.keyboard("{ArrowDown}");
      expect(input).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: /生成分镜图/ }).id);
      await user.keyboard("{Enter}");

      expect(input).toHaveValue("先 /generate-storyboard ");
      expect(sendMessage).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    });

    it("closes the skill menu on Escape without sending", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({ skills: SKILLS });
      render(<AgentCopilot />);

      const input = screen.getByRole("combobox", { name: "Agent 输入" });
      await user.type(input, "/");
      await screen.findByRole("listbox");
      await user.keyboard("{Escape}");

      await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
      expect(input).toHaveValue("/");
    });

    it("while the agent replies, one button stops on an empty input and sends once there is content", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({ currentSessionId: "session-1", sessionStatus: "running" });
      render(<AgentCopilot />);

      await user.click(screen.getByRole("button", { name: "停止回复" }));
      expect(interrupt).toHaveBeenCalledTimes(1);

      const input = screen.getByRole("combobox", { name: "Agent 输入" });
      await user.type(input, "结尾再加一个空镜");
      expect(screen.queryByRole("button", { name: "停止回复" })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "发送消息" }));

      expect(sendMessage).toHaveBeenCalledWith("结尾再加一个空镜", undefined);
    });

    it("stacks queued messages above the input in send order", () => {
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: [
          { id: "q-1", content: [{ type: "text", text: "第 3 镜改成黄昏" }], state: "queued" },
          {
            id: "q-2",
            content: [{ type: "image", source: { type: "base64", media_type: "image/png", data: "AAAA" } }],
            state: "queued",
          },
        ],
      });
      render(<AgentCopilot />);

      const items = within(screen.getByRole("list", { name: "排队消息" })).getAllByRole("listitem");
      expect(items).toHaveLength(2);
      expect(items[0]).toHaveTextContent("第 3 镜改成黄昏");
      expect(items[0]).toHaveTextContent("排队中");
      expect(items[1]).toHaveTextContent("1 张图片");
    });

    it("editing a queued message appends its text and images after what is already in the input", async () => {
      const user = userEvent.setup();
      const content = [
        { type: "image" as const, source: { type: "base64" as const, media_type: "image/png", data: "AAAA" } },
        { type: "text" as const, text: "第 3 镜改成黄昏" },
      ];
      // 撤回成功时 hook 把内容交给输入框
      withdrawQueuedMessage.mockImplementationOnce(async () => {
        useAssistantStore.getState().appendToComposer(content);
      });
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: [{ id: "q-1", content, state: "queued" }],
      });
      render(<AgentCopilot />);
      const input = screen.getByRole("combobox", { name: "Agent 输入" });
      await user.type(input, "片尾加字幕");

      const item = within(screen.getByRole("list", { name: "排队消息" })).getByRole("listitem");
      await user.click(within(item).getByRole("button", { name: "编辑" }));

      expect(withdrawQueuedMessage).toHaveBeenCalledWith("q-1", "edit");
      await waitFor(() => expect(input).toHaveValue("片尾加字幕\n第 3 镜改成黄昏"));
      expect(screen.getByRole("button", { name: "放大图片附件 1" })).toBeInTheDocument();
    });

    it("content returned by an edit while a send is in flight stays in the input after the send is accepted", async () => {
      const user = userEvent.setup();
      const image = (data: string) => ({
        type: "image" as const,
        source: { type: "base64" as const, media_type: "image/png", data },
      });
      const first = [image("AAAA"), { type: "text" as const, text: "第 3 镜改成黄昏" }];
      const second = [image("BBBB"), { type: "text" as const, text: "片尾加字幕" }];
      withdrawQueuedMessage.mockImplementationOnce(async () => {
        useAssistantStore.getState().appendToComposer(first);
      });
      let returnSecond!: () => void;
      withdrawQueuedMessage.mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            returnSecond = () => {
              useAssistantStore.getState().appendToComposer(second);
              resolve();
            };
          }),
      );
      let acceptSend!: () => void;
      sendMessage.mockImplementationOnce(() => {
        useAssistantStore.setState({ sending: true });
        return new Promise<boolean>((resolve) => {
          acceptSend = () => {
            useAssistantStore.setState({ sending: false });
            resolve(true);
          };
        });
      });
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: [
          { id: "q-1", content: first, state: "queued" },
          { id: "q-2", content: second, state: "queued" },
        ],
      });
      render(<AgentCopilot />);
      const input = screen.getByRole("combobox", { name: "Agent 输入" });
      const tray = screen.getByRole("list", { name: "排队消息" });
      await user.click(within(within(tray).getAllByRole("listitem")[0]).getByRole("button", { name: "编辑" }));
      await waitFor(() => expect(input).toHaveValue("第 3 镜改成黄昏"));

      // 第二条的编辑还在等撤回答复时发出输入框里的内容；答复在发送受理前到达，内容追加进输入框
      await user.click(within(within(tray).getAllByRole("listitem")[1]).getByRole("button", { name: "编辑" }));
      await user.click(screen.getByRole("button", { name: "发送消息" }));
      expect(sendMessage).toHaveBeenCalledWith("第 3 镜改成黄昏", [expect.objectContaining({ mimeType: "image/png" })]);
      act(() => returnSecond());
      await waitFor(() => expect(input).toHaveValue("第 3 镜改成黄昏\n片尾加字幕"));
      act(() => acceptSend());

      // 只清掉已发出的文字和图片，发送途中退回的内容留在输入框
      await waitFor(() => expect(input).toHaveValue("片尾加字幕"));
      expect(screen.getByRole("button", { name: "放大图片附件 1" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "放大图片附件 2" })).not.toBeInTheDocument();
    });

    it("deleting a queued message withdraws it without touching the input", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: [{ id: "q-1", content: [{ type: "text", text: "第 3 镜改成黄昏" }], state: "queued" }],
      });
      render(<AgentCopilot />);

      await user.click(screen.getByRole("button", { name: "删除" }));

      expect(withdrawQueuedMessage).toHaveBeenCalledWith("q-1", "delete");
      expect(screen.getByRole("combobox", { name: "Agent 输入" })).toHaveValue("");
    });

    it("explains unsent messages and offers send, edit and delete on each of them", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "error",
        queuedMessages: [
          { id: "q-1", content: [{ type: "text", text: "第 3 镜改成黄昏" }], state: "unsent" },
          { id: "q-2", content: [{ type: "text", text: "加一段旁白" }], state: "queued" },
        ],
      });
      render(<AgentCopilot />);

      expect(screen.getByText("会话已中断，以下消息尚未发送")).toBeInTheDocument();
      const [unsent, queued] = within(screen.getByRole("list", { name: "排队消息" })).getAllByRole("listitem");
      expect(within(unsent).getByText("未发送")).toBeInTheDocument();
      expect(within(queued).queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
      // 未发送的消息不在 CLI 队列里，没有轮次可打断：只有发送，没有立即发送
      expect(within(unsent).queryByRole("button", { name: "立即发送" })).not.toBeInTheDocument();

      await user.click(within(unsent).getByRole("button", { name: "发送" }));
      expect(resendQueuedMessage).toHaveBeenCalledWith("q-1");
      await user.click(within(unsent).getByRole("button", { name: "编辑" }));
      expect(withdrawQueuedMessage).toHaveBeenCalledWith("q-1", "edit");
      await user.click(within(unsent).getByRole("button", { name: "删除" }));
      expect(withdrawQueuedMessage).toHaveBeenCalledWith("q-1", "delete");
      expect(screen.getByRole("combobox", { name: "Agent 输入" })).toHaveValue("");
    });

    it("shows no interruption notice while every message is still queued", () => {
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: [{ id: "q-1", content: [{ type: "text", text: "加一段旁白" }], state: "queued" }],
      });
      render(<AgentCopilot />);

      expect(screen.queryByText("会话已中断，以下消息尚未发送")).not.toBeInTheDocument();
    });

    it("sends a queued message now so the agent handles it first", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: [{ id: "q-1", content: [{ type: "text", text: "先停下改结局" }], state: "queued" }],
      });
      render(<AgentCopilot />);

      const actions = within(screen.getByRole("list", { name: "排队消息" })).getAllByRole("button");
      expect(actions.map((button) => button.getAttribute("aria-label"))).toEqual(["立即发送", "编辑", "删除"]);
      await user.click(screen.getByRole("button", { name: "立即发送" }));

      expect(sendQueuedMessageNow).toHaveBeenCalledWith("q-1");
      expect(withdrawQueuedMessage).not.toHaveBeenCalled();
    });

    it("keeps the tray while the agent asks a question, without sending now but still editable and deletable", async () => {
      const user = userEvent.setup();
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        pendingQuestion: makePendingQuestion(),
        queuedMessages: [{ id: "q-1", content: [{ type: "text", text: "第 3 镜改成黄昏" }], state: "queued" }],
      });
      render(<AgentCopilot />);

      expect(screen.getByRole("form", { name: "Agent 的提问" })).toBeInTheDocument();
      const tray = screen.getByRole("list", { name: "排队消息" });
      expect(within(tray).getByText("第 3 镜改成黄昏")).toBeVisible();
      expect(within(tray).queryByRole("button", { name: "立即发送" })).not.toBeInTheDocument();

      await user.click(within(tray).getByRole("button", { name: "删除" }));
      expect(withdrawQueuedMessage).toHaveBeenCalledWith("q-1", "delete");
      await user.click(within(tray).getByRole("button", { name: "编辑" }));
      expect(withdrawQueuedMessage).toHaveBeenCalledWith("q-1", "edit");
    });

    it("scrolls a long queue inside the tray and follows the newest message, leaving the input and stop button in place", () => {
      // jsdom 没有布局：托盘按 160px 高、每条按 40px 高打桩，滚动位置按元素记住
      const scrollTops = new WeakMap<Element, number>();
      const isTray = (el: Element) => el.getAttribute("aria-label") === "排队消息";
      const indexInTray = (el: Element) =>
        el.parentElement && isTray(el.parentElement) ? Array.from(el.parentElement.children).indexOf(el) : -1;
      vi.spyOn(Element.prototype, "clientHeight", "get").mockImplementation(function (this: Element) {
        return isTray(this) ? 160 : 0;
      });
      vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockImplementation(function (this: HTMLElement) {
        return Math.max(0, indexInTray(this)) * 40;
      });
      vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) {
        return indexInTray(this) >= 0 ? 40 : 0;
      });
      vi.spyOn(Element.prototype, "scrollTop", "get").mockImplementation(function (this: Element) {
        return scrollTops.get(this) ?? 0;
      });
      vi.spyOn(Element.prototype, "scrollTop", "set").mockImplementation(function (this: Element, value: number) {
        scrollTops.set(this, value);
      });
      const queued = (index: number) => ({
        id: `q-${index}`,
        content: [{ type: "text" as const, text: `第 ${index} 条补充要求` }],
        state: "queued" as const,
      });
      useAssistantStore.setState({
        currentSessionId: "session-1",
        sessionStatus: "running",
        queuedMessages: Array.from({ length: 20 }, (_, index) => queued(index + 1)),
      });
      render(<AgentCopilot />);

      const tray = screen.getByRole("list", { name: "排队消息" });
      expect(tray).toHaveClass("max-h-[30cqh]", "overflow-y-auto");
      expect(tray).toHaveAttribute("tabindex", "0");
      expect(tray.scrollTop).toBe(640);
      expect(screen.getByRole("combobox", { name: "Agent 输入" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "停止回复" })).toBeEnabled();

      // 用户把托盘滚回顶部后，最前面的消息被接纳：不跳动；再发一条：滚到新消息
      tray.scrollTop = 0;
      act(() => useAssistantStore.setState((s) => ({ queuedMessages: s.queuedMessages.slice(1) })));
      expect(tray.scrollTop).toBe(0);
      act(() => useAssistantStore.setState((s) => ({ queuedMessages: [...s.queuedMessages, queued(21)] })));
      expect(tray.scrollTop).toBe(640);
    });
  });

  it("does not send when Enter is used to confirm an IME composition", () => {
    render(<AgentCopilot />);

    const textarea = screen.getByRole("combobox", { name: "Agent 输入" });
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

    expect(screen.getByRole("combobox", { name: "Agent 输入" })).toHaveValue("为第 1 集生成剧本");

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

      const input = screen.getByRole("combobox", { name: "Agent 输入" });
      fireEvent.change(input, { target: { value: "继续" } });
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

      expect(sendMessage).toHaveBeenCalledWith("继续", undefined);
      expect(viewport.scrollTop).toBe(end);
      await waitFor(() => expect(jump).toHaveAttribute("data-active", "false"));
    });
  });
});
